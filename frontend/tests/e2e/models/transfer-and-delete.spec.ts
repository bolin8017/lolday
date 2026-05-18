import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";
import { ModelPage } from "../helpers/model.po";

/**
 * D3.3 — critical user flow: model transfer + delete.
 *
 * The seeded fixture model is registered against detector
 * "elfrfdet-fixture" by the calling admin persona, so the detail URL is
 * `/models/admin/elfrfdet-fixture` initially and `/models/dev/elfrfdet-
 * fixture` after the transfer. The list row text follows the
 * `{owner}/{name}` Link in `_authed.models._index.tsx`, which renders
 * the detector name — not a "fixture-model" literal.
 *
 * Re-seed at the top keeps the spec replay-safe (the seed endpoint is
 * idempotent — re-POSTing returns the same IDs; if the previous run
 * deleted the row, this re-creates it owned by admin).
 */
const DETECTOR_NAME = "elfrfdet-fixture";

test("transfer model from admin to developer, then delete", async ({
  browser,
}) => {
  const ctx = await browser.newContext({
    extraHTTPHeaders: { "X-Dev-Persona": "admin" },
  });
  const seedResp = await ctx.request.post("/api/v1/dev/seed-fixtures");
  const seed = await seedResp.json();
  await ctx.close();

  // ── admin transfers to developer (handle "dev", not the email).
  const adminCtx = await browser.newContext({
    extraHTTPHeaders: { "X-Dev-Persona": "admin" },
  });
  const adminPage = await adminCtx.newPage();
  await loginAs(adminPage, "admin");

  const adminModel = new ModelPage(adminPage);
  await adminModel.gotoDetail("admin", DETECTOR_NAME);
  await adminModel.transferTo("dev");

  await adminModel.gotoList();
  await expect(
    adminPage.getByRole("row").filter({ hasText: new RegExp(DETECTOR_NAME) }),
  ).toHaveCount(0);
  await adminCtx.close();

  // ── developer sees + deletes.
  const devCtx = await browser.newContext({
    extraHTTPHeaders: { "X-Dev-Persona": "developer" },
  });
  const devPage = await devCtx.newPage();
  await loginAs(devPage, "developer");

  const devModel = new ModelPage(devPage);
  await devModel.gotoList();
  await expect(
    devPage.getByRole("row").filter({ hasText: new RegExp(DETECTOR_NAME) }),
  ).toBeVisible();
  await devModel.gotoDetail("dev", DETECTOR_NAME);
  await devModel.deleteModel("dev", DETECTOR_NAME);

  await devModel.gotoList();
  await expect(
    devPage.getByRole("row").filter({ hasText: new RegExp(DETECTOR_NAME) }),
  ).toHaveCount(0);
  await devCtx.close();
  expect(seed.model_version_id).toBeTruthy();
});
