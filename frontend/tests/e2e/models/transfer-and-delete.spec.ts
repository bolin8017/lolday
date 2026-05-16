import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";
import { ModelPage } from "../helpers/model.po";

/**
 * D3.3 — critical user flow: model transfer + delete.
 *
 * Re-seed in setup keeps the spec replay-safe (the seed endpoint is
 * idempotent — re-POSTing returns the same IDs and re-asserts the model
 * is owned by admin@dev.local).
 */
test("transfer model from admin to developer, then delete", async ({
  browser,
}) => {
  // Re-seed so the model_version is owned by admin again.
  const ctx = await browser.newContext({
    extraHTTPHeaders: { "X-Dev-Persona": "admin" },
  });
  const seedResp = await ctx.request.post("/api/v1/dev/seed-fixtures");
  const seed = await seedResp.json();
  await ctx.close();

  // ── admin transfers to developer
  const adminCtx = await browser.newContext({
    extraHTTPHeaders: { "X-Dev-Persona": "admin" },
  });
  const adminPage = await adminCtx.newPage();
  await loginAs(adminPage, "admin");

  const adminModel = new ModelPage(adminPage);
  await adminModel.gotoDetail("fixture", "fixture-model");
  await adminModel.transferTo("dev@dev.local");

  await adminModel.gotoList();
  await expect(
    adminPage.getByRole("row").filter({ hasText: /fixture-model/i }),
  ).toHaveCount(0);
  await adminCtx.close();

  // ── developer sees + deletes
  const devCtx = await browser.newContext({
    extraHTTPHeaders: { "X-Dev-Persona": "developer" },
  });
  const devPage = await devCtx.newPage();
  await loginAs(devPage, "developer");

  const devModel = new ModelPage(devPage);
  await devModel.gotoList();
  await expect(
    devPage.getByRole("row").filter({ hasText: /fixture-model/i }),
  ).toBeVisible();
  await devModel.gotoDetail("dev", "fixture-model");
  await devModel.deleteModel();

  await devModel.gotoList();
  await expect(
    devPage.getByRole("row").filter({ hasText: /fixture-model/i }),
  ).toHaveCount(0);
  await devCtx.close();
  expect(seed.model_version_id).toBeTruthy();
});
