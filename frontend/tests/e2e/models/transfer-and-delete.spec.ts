import { test, expect } from "@playwright/test";

import { loginAs, reseedAsAdmin } from "../helpers";
import { ModelPage } from "../helpers/model.po";

/**
 * D3.3 — critical user flow: model transfer + delete.
 *
 * The spec is DESTRUCTIVE — it transfers ownership and deletes the
 * `RegisteredModel`. To stop parallel read-only specs
 * (`model-transition.spec.ts`, `mobile/model-list.spec.ts`, …) from
 * racing the destructive window, the spec targets a SEPARATE throwaway
 * detector + model seeded by `POST /api/v1/dev/seed-fixtures`
 * (`throwaway-fixture` / `throwaway-model-fixture`). The primary
 * shared fixture (`elfrfdet-fixture`) is never touched.
 *
 * History: iter 1 (PR #288) made the spec work end-to-end against the
 * SHARED fixture. Iter 5 (PR #291) added an `afterEach` re-seed to
 * mitigate the between-test window, but the during-test window stayed
 * open — run 26040409369 (2026-05-18 14:40) caught
 * `model-transition` with "Model not found." on the shared
 * `/models/admin/elfrfdet-fixture` URL because the parallel transfer
 * had just flipped ownership to dev. Iter 11 (this rewrite) adds the
 * throwaway fixture so the destructive flow runs against
 * `/models/admin/throwaway-fixture`, leaving the shared URL stable.
 *
 * Detail URL pattern: `/models/{owner_handle}/{detector_name}`. The
 * throwaway detector name is `throwaway-fixture` (per `dev_seed.py`).
 * List rows render `{owner}/{detector_name}`.
 */
const THROWAWAY_DETECTOR_NAME = "throwaway-fixture";

test.afterEach(async ({ browser }) => {
  // Re-seed restores both the primary AND throwaway rows. If the spec
  // exited mid-flight (e.g. transfer succeeded but delete failed) the
  // throwaway model could be in an unexpected ownership state — the
  // seed is idempotent on the row's UUID5 PK so the next run resumes
  // cleanly.
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

  // ── admin transfers throwaway model to developer (handle "dev").
  const adminCtx = await browser.newContext({
    extraHTTPHeaders: { "X-Dev-Persona": "admin" },
  });
  const adminPage = await adminCtx.newPage();
  await loginAs(adminPage, "admin");

  const adminModel = new ModelPage(adminPage);
  await adminModel.gotoDetail("admin", THROWAWAY_DETECTOR_NAME);
  await adminModel.transferTo("dev");
  await adminCtx.close();

  // ── developer sees the model + deletes it.
  const devCtx = await browser.newContext({
    extraHTTPHeaders: { "X-Dev-Persona": "developer" },
  });
  const devPage = await devCtx.newPage();
  await loginAs(devPage, "developer");

  const devModel = new ModelPage(devPage);
  await devModel.gotoList();
  await expect(
    devPage
      .getByRole("row")
      .filter({ hasText: new RegExp(THROWAWAY_DETECTOR_NAME) }),
  ).toBeVisible();
  await devModel.gotoDetail("dev", THROWAWAY_DETECTOR_NAME);
  await devModel.deleteModel("dev", THROWAWAY_DETECTOR_NAME);

  await devModel.gotoList();
  await expect(
    devPage
      .getByRole("row")
      .filter({ hasText: new RegExp(THROWAWAY_DETECTOR_NAME) }),
  ).toHaveCount(0);
  await devCtx.close();
  expect(seed.throwaway_model_version_id).toBeTruthy();
});
