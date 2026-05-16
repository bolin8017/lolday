import { test, expect } from "@playwright/test";

import { loginAs } from "../e2e/helpers";

/**
 * §10 #30 (D2.7) — RJSF visual snapshot.
 *
 * Initially `.skip` so CI doesn't report missing-baseline failures on
 * first run. Operator removes `.skip` after `--update-snapshots`.
 */
test.skip("rjsf form section renders pixel-stable", async ({ page }) => {
  await loginAs(page, "admin");
  await page.goto("/jobs/new");
  await page
    .getByText(/^Detector$/, { exact: true })
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option").first().click();
  await page
    .getByText(/^Version$/, { exact: true })
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option").first().click();
  await page.waitForLoadState("networkidle");

  const rjsf = page.locator(".rjsf-wrap").first();
  await expect(rjsf).toHaveScreenshot("rjsf-form.png", {
    animations: "disabled",
  });
});
