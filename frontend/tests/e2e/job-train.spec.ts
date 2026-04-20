import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("submit train job and see it succeed", async ({ page }) => {
  test.setTimeout(10 * 60_000);
  await login(page);

  await page.goto("/jobs/new");

  // Pick detector + version (upxelfdet v0.5.0 built in Task 22)
  await page.getByText(/^Detector$/).locator("..").getByRole("combobox").click();
  await page.getByRole("option", { name: /upx/i }).click();
  await page.getByText(/^Version$/).locator("..").getByRole("combobox").click();
  await page.getByRole("option", { name: /v0\.5\.0/ }).click();

  // Pick train + test datasets (any available dataset — the admin's uploaded one from Task 25 or earlier)
  const datasetPickers = page.locator('[role="combobox"]');
  // After detector + version selection, indexes 2 and 3 are train + test dataset combos
  await datasetPickers.nth(2).click();
  await page.getByRole("option").first().click();
  await datasetPickers.nth(3).click();
  await page.getByRole("option").first().click();

  // Submit (RJSF form defaults should satisfy the schema)
  await page.getByRole("button", { name: /submit job/i }).click();
  await page.waitForURL(/\/jobs\/[0-9a-f-]+/, { timeout: 15_000 });

  // Wait for succeeded (the new job's StatusBadge flips, or an old succeeded Metric appears)
  await expect(page.getByText(/succeeded/i).first()).toBeVisible({ timeout: 8 * 60_000 });
});
