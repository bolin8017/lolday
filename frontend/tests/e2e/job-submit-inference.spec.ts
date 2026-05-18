import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("predict: choosing a model version auto-derives detector and renders its hyperparameter form", async ({
  page,
}) => {
  await login(page);
  await page.goto("/jobs/new");

  await page.getByRole("button", { name: /^Predict$/i }).click();

  // Initial: Hyperparameters card shows the "pick a model version"
  // placeholder until a model + version selection lets the form fetch
  // the derived detector's manifest.
  await expect(
    page.getByText(/Pick a model version to load its hyperparameter form/i),
  ).toBeVisible();

  // Target the SelectTrigger by its aria-label, not via the
  // text→parent→combobox chain. The Inference subform renders
  // "Source model" both as `<CardTitle>` AND as the `<Label>`
  // (`InferenceSubForm.tsx:89,94`), so `getByText` strict-mode returns
  // multiple matches and `.locator('..').getByRole('combobox')` resolves
  // to two candidate trees — Playwright times out the click. aria-label
  // on the SelectTrigger is unique per combobox.
  await page.getByRole("combobox", { name: "Source model" }).click();
  await page.getByRole("option").first().click();

  await page.getByRole("combobox", { name: "Model version" }).click();
  await page.getByRole("option").first().click();

  // Auto-derivation signal: PR #115 hid the visible "Detector (derived
  // from model)" card (it duplicated info that a model already implies),
  // so the observable signal moves to the Hyperparameters card. After
  // the model+version pick, the derivedDetectorId / derivedDetectorVersionTag
  // state is set, `useDetectorVersion` fetches the manifest, and the
  // RJSF form renders the stage's params_schema. The placeholder
  // disappears and the schema-driven `batch_size` input appears (seeded
  // by `dev_seed.py` fixture manifest).
  await expect(
    page.getByText(/Pick a model version to load its hyperparameter form/i),
  ).not.toBeVisible();
  await expect(page.getByLabel(/batch_size/i)).toBeVisible();
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
