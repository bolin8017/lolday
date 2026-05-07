import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("predict: choosing a model version auto-fills detector (derived from model)", async ({
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

  // Detector (derived from model) card title is visible
  await expect(
    page.getByText(/Detector \(derived from model\)/i),
  ).toBeVisible();

  // The derived version tag renders inside a read-only <code> element —
  // no advanced override toggle, no editable version dropdown
  await expect(page.locator("code").first()).toBeVisible();
});

test("evaluate: detector version is read-only (no override toggle)", async ({
  page,
}) => {
  await login(page);
  await page.goto("/jobs/new");

  await page.getByRole("button", { name: /^Evaluate$/i }).click();

  // No "Advanced: override detector version" button anywhere on the page
  await expect(
    page.getByRole("button", {
      name: /Advanced: override detector version|進階：覆寫 detector version/i,
    }),
  ).toHaveCount(0);
});
