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
 * This spec is destructive against the shared seeded fixture
 * (delete cascades both `RegisteredModel` and its `ModelVersion`).
 * `mobile/model-list` and other parallel specs expect the model to
 * exist, so we both:
 *   - re-seed at the top (in case a prior run left no row), and
 *   - re-seed via `test.afterEach` at the end (so subsequent parallel
 *     specs see the model again immediately).
 * The seed endpoint is idempotent (UUID5-keyed `session.get` + early
 * return), so re-POSTing is cheap.
 *
 * There is still a race window between the delete and the afterEach
 * re-seed where a parallel spec could see no model. A proper fix
 * needs either per-test fixture isolation (separate model name per
 * worker) or worker-scoped serial execution for destructive specs —
 * that's a worker-storage / test.serial refactor tracked separately.
 */
const DETECTOR_NAME = "elfrfdet-fixture";

async function reseedAsAdmin(browser: import("@playwright/test").Browser) {
  const ctx = await browser.newContext({
    extraHTTPHeaders: { "X-Dev-Persona": "admin" },
  });
  await ctx.request.post("/api/v1/dev/seed-fixtures");
  await ctx.close();
}

test.afterEach(async ({ browser }) => {
  // Restore the shared seed for parallel specs (mobile/model-list,
  // mobile/job-submit, etc.) that depend on the fixture rows existing.
  await reseedAsAdmin(browser);
});

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
