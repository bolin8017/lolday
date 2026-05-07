import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("predict: choosing a model version auto-fills detector (derived)", async ({
  page,
}) => {
  await login(page);
  await page.goto("/jobs/new");

  await page.getByRole("button", { name: /^Predict$/i }).click();

  // Pick a source model — the SelectTrigger under "Source model" label
  await page
    .getByText(/^Source model$/, { exact: true })
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option").first().click();

  // Pick a model version
  await page
    .getByText(/^Model version$/, { exact: true })
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option").first().click();

  // Detector (derived) card title is visible
  await expect(page.getByText(/Detector \(derived\)/i)).toBeVisible();

  // The derived version tag renders inside a <code> element
  await expect(page.locator("code").first()).toBeVisible();
});

test("evaluate: advanced override toggle reveals version dropdown", async ({
  page,
}) => {
  await login(page);
  await page.goto("/jobs/new");

  await page.getByRole("button", { name: /^Evaluate$/i }).click();

  // Pick a source model
  await page
    .getByText(/^Source model$/, { exact: true })
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option").first().click();

  // Pick a model version so the derived detector is populated
  await page
    .getByText(/^Model version$/, { exact: true })
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option").first().click();

  // Click the Advanced override toggle button
  await page
    .getByRole("button", {
      name: /Advanced: override detector version|進階：覆寫 detector version/i,
    })
    .click();

  // After expanding, the version SelectTrigger inside the Detector (derived)
  // card becomes visible (a combobox with "Pick version" placeholder).
  await expect(
    page
      .getByRole("combobox", { name: /Pick version/i })
      .or(page.locator('[placeholder="Pick version"]')),
  ).toBeVisible();
});
