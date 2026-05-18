import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";
import { DetectorPage } from "../helpers/detector.po";

/**
 * D3.3 — critical user flow: detector list + detail + trigger build.
 *
 * Validates list visibility, detail render, version row presence, and
 * the POST /api/v1/builds wiring. The K8s BuildKit dispatch path is
 * covered by chart-e2e.yml.
 */
test("detector list + detail + trigger build", async ({ page }) => {
  await loginAs(page, "admin");

  const det = new DetectorPage(page);
  await det.gotoList();
  await expect(
    page.getByRole("row").filter({ hasText: /elfrfdet/i }),
  ).toBeVisible();

  const seedResp = await page.request.post("/api/v1/dev/seed-fixtures");
  const seed = await seedResp.json();
  await det.gotoDetail(seed.detector_id);
  // The detail page opens on the Overview tab; the version table renders
  // inside the Versions tab. Switch tabs before asserting on the row.
  await det.openVersionsTab();
  await expect(det.versionRow("v1.0.0-fixture")).toBeVisible();

  // The "+ Trigger build" button lives inside the Builds tab. The
  // backend POST URL is `/api/v1/detectors/{id}/builds` (not
  // `/api/v1/builds` — that flat alias is a GET-only convenience for
  // polling scripts, see `routers/builds.py`).
  await det.openBuildsTab();
  const buildResp = page.waitForResponse(
    (resp) =>
      /\/api\/v1\/detectors\/[^/]+\/builds$/.test(resp.url()) &&
      resp.request().method() === "POST",
  );
  await det.triggerBuild();
  await det.confirmBuildDialog("v1.0.0-fixture");
  const resp = await buildResp;
  expect([200, 201, 202]).toContain(resp.status());
});
