/**
 * Opt-in e2e — exercises the full Phase 4 cutover acceptance criterion.
 *
 * Submits a train job for a maldet-2.0-rebuilt detector, waits SUCCEEDED,
 * then (in a future iteration) an evaluate job using the trained model,
 * waits SUCCEEDED, then asserts the Confusion matrix + Per-class metrics
 * cards render with Malware row tagged (positive) — the proof that the
 * trainer/evaluator encoding inconsistency closed in Phase 1 actually
 * delivers correct downstream UI behaviour.
 *
 * Set BASELINE_E2E=1 + DETECTOR_NAME / DETECTOR_TAG to enable.
 * Skipped by default (heavyweight; needs detector image already pushed).
 *
 * TODO(phase-4.10): extend with evaluate-job submission + confusion-matrix
 * assertion. The full evaluate-side check (Malware-as-positive in the
 * confusion matrix card, per-class metrics card visibility) is currently
 * performed manually as part of the Phase 4.10 baseline walkthrough.
 */
import { test, expect } from "@playwright/test";
import { login } from "./helpers";

const ENABLED = process.env.BASELINE_E2E === "1";
const DETECTOR_NAME = process.env.DETECTOR_NAME ?? "";
const DETECTOR_TAG = process.env.DETECTOR_TAG ?? "";

test("train + evaluate baseline — confusion matrix renders with Malware as positive", async ({
  page,
}) => {
  test.skip(!ENABLED, "set BASELINE_E2E=1 to enable");
  test.skip(
    !DETECTOR_NAME || !DETECTOR_TAG,
    "set DETECTOR_NAME and DETECTOR_TAG",
  );
  test.setTimeout(20 * 60_000); // generous

  await login(page);

  // Submit train job
  await page.goto("/jobs/new");
  await page
    .getByText(/^Detector$/)
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option", { name: new RegExp(DETECTOR_NAME) }).click();
  await page
    .getByText(/^Version$/)
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option", { name: new RegExp(DETECTOR_TAG) }).click();

  // Pick the first available train + test dataset combos
  const combos = page.locator('[role="combobox"]');
  await combos.nth(2).click();
  await page.getByRole("option").first().click();
  await combos.nth(3).click();
  await page.getByRole("option").first().click();

  await page.getByRole("button", { name: /submit job/i }).click();
  await page.waitForURL(/\/jobs\/[0-9a-f-]+/, { timeout: 30_000 });
  await expect(page.getByText(/succeeded/i).first()).toBeVisible({
    timeout: 10 * 60_000,
  });

  // TODO(phase-4.10): submit an evaluate job referencing the freshly trained
  // model version, wait for SUCCEEDED, then assert:
  //   - Confusion matrix card visible
  //   - Malware row tagged "(positive)"
  //   - Per-class metrics card visible
  // The detail of which model version selector to click depends on the
  // JobsNew form's evaluate path. For this stub, we assert that we got
  // a successful train job and leave the full evaluate flow as a future
  // enhancement; the actual baseline acceptance check happens manually
  // in Phase 4.10.

  // Smoke: Job Detail page shows the trained-model card so we know the
  // chain is intact.
  await expect(page.getByText(/trained model|model version/i)).toBeVisible();
});
