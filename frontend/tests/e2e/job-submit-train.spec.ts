import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("train: detector + version + train dataset, no test dataset, can submit", async ({
  page,
}) => {
  await login(page);
  await page.goto("/jobs/new");

  // Select the Train job type (default, but click explicitly for clarity)
  await page.getByRole("button", { name: /^Train$/i }).click();

  // Pick a detector — click the SelectTrigger under the "Detector" label
  await page
    .getByText(/^Detector$/, { exact: true })
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option").first().click();

  // Pick a version
  await page
    .getByText(/^Version$/, { exact: true })
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option").first().click();

  // Pick a train dataset
  await page
    .getByText(/^Train dataset$/, { exact: true })
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option").first().click();

  // Test dataset is optional — do NOT pick one.
  // Submit button must be enabled.
  await expect(page.getByRole("button", { name: /submit job/i })).toBeEnabled();
});

test("train: clearing the Test dataset clears the value", async ({ page }) => {
  await login(page);
  await page.goto("/jobs/new");

  await page.getByRole("button", { name: /^Train$/i }).click();

  // Fill in the required fields first
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

  await page
    .getByText(/^Train dataset$/, { exact: true })
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option").first().click();

  // Pick a test dataset. Locating via aria-label rather than the
  // `getByText('Test dataset').locator('..').getByRole('combobox')` chain
  // the other Selects use because the Test-dataset Label sits inside an
  // extra `<div class="flex items-center gap-1">` wrapper alongside the
  // HelpHint — the combobox is a sibling of that wrapper, not a
  // descendant of the Label's parent. Using the SelectTrigger's
  // aria-label (added in #226) is the more robust locator anyway.
  await page.getByRole("combobox", { name: /Test dataset/i }).click();
  await page.getByRole("option").first().click();

  // Clear it via the ClearableSelect X button (aria-label="Clear")
  await page.getByRole("button", { name: /^Clear$/i }).click();

  // After clearing, the Test dataset placeholder is visible again
  await expect(page.getByText(/Pick dataset \(optional\)/i)).toBeVisible();
});
