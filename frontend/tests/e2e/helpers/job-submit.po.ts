/**
 * D3.1 — JobSubmitPage page object model.
 *
 * Factors the comboboxes-via-label-locator pattern out of
 * job-submit-train.spec.ts / job-submit-inference.spec.ts. New job-submit
 * E2E specs (Task 9 full-lifecycle, Task 6 admin-only-actions) compose
 * methods rather than re-inlining the selector soup. If the form's
 * labelled-combobox shape changes (e.g. RJSF v6 → v7), the fix is here,
 * not across every spec.
 */
import type { Page } from "@playwright/test";

type JobType = "Train" | "Evaluate" | "Predict";

export class JobSubmitPage {
  constructor(private readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto("/jobs/new");
  }

  async selectJobType(type: JobType): Promise<void> {
    await this.page
      .getByRole("button", { name: new RegExp(`^${type}$`, "i") })
      .click();
  }

  /**
   * Open the labelled combobox and pick an option. Without `optionName`,
   * clicks the first option (matches existing `job-train` /
   * `full-lifecycle` specs that don't care which option lands). Pass
   * `optionName` to pin the choice — required when parallel specs can
   * leak entries that sort before the seeded fixture (e.g.
   * `dataset-upload.spec.ts` creates `e2e-<timestamp>` datasets that
   * sort before `fixture-train` in the combobox).
   */
  private async pickByLabel(label: string, optionName?: string): Promise<void> {
    await this.page
      .getByText(new RegExp(`^${label}$`, "i"), { exact: true })
      .locator("..")
      .getByRole("combobox")
      .click();
    if (optionName) {
      await this.page
        .getByRole("option", { name: new RegExp(optionName, "i") })
        .click();
    } else {
      await this.page.getByRole("option").first().click();
    }
  }

  async pickDetector(optionName?: string): Promise<void> {
    await this.pickByLabel("Detector", optionName);
  }

  async pickVersion(optionName?: string): Promise<void> {
    await this.pickByLabel("Version", optionName);
  }

  async pickTrainDataset(optionName?: string): Promise<void> {
    await this.pickByLabel("Train dataset", optionName);
  }

  async pickTestDataset(optionName?: string): Promise<void> {
    await this.pickByLabel("Test dataset", optionName);
  }

  submitButton() {
    return this.page.getByRole("button", { name: /submit job/i });
  }

  async submit(): Promise<void> {
    await this.submitButton().click();
  }
}
