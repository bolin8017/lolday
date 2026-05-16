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
  await expect(det.versionRow("v1.0.0-fixture")).toBeVisible();

  const buildResp = page.waitForResponse(
    (resp) =>
      resp.url().includes("/api/v1/builds") &&
      resp.request().method() === "POST",
  );
  await det.triggerBuild();
  const resp = await buildResp;
  expect([200, 202]).toContain(resp.status());
});
