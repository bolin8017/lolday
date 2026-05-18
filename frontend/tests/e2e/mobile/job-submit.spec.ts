import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";
import { JobSubmitPage } from "../helpers/job-submit.po";

/**
 * D3.7 — mobile job-submit flow.
 *
 * Mobile viewport (iPhone 13 Mini, 393×812). Asserts submit button
 * is reachable + tappable (≥40px touch target).
 */
test("mobile: train job submit flow", async ({ page }) => {
  await loginAs(page, "admin");

  const submit = new JobSubmitPage(page);
  await submit.goto();
  await submit.selectJobType("Train");
  // Pin to the seeded fixture so a parallel `dataset-upload.spec.ts`
  // run (which creates `e2e-<timestamp>` datasets that sort before
  // `fixture-train` in the combobox) can't poison the form choice and
  // 422 the POST — that was the failure mode that left the URL stuck
  // at `/jobs/new`.
  await submit.pickDetector("ELF RF Detector");
  await submit.pickVersion("v1.0.0-fixture");
  await submit.pickTrainDataset("fixture-train");

  const button = submit.submitButton();
  await expect(button).toBeEnabled();
  const box = await button.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(40);

  await submit.submit();
  await expect(page).toHaveURL(/\/jobs(\/[a-f0-9-]+)?$/);
});
