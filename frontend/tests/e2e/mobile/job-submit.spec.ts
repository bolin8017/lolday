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
  await submit.pickDetector();
  await submit.pickVersion();
  await submit.pickTrainDataset();

  const button = submit.submitButton();
  await expect(button).toBeEnabled();
  const box = await button.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(40);

  await submit.submit();
  await expect(page).toHaveURL(/\/jobs(\/[a-f0-9-]+)?$/);
});
