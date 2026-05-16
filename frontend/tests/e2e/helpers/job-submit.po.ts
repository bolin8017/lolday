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

  private async pickByLabel(label: string): Promise<void> {
    await this.page
      .getByText(new RegExp(`^${label}$`, "i"), { exact: true })
      .locator("..")
      .getByRole("combobox")
      .click();
    await this.page.getByRole("option").first().click();
  }

  async pickDetector(): Promise<void> {
    await this.pickByLabel("Detector");
  }

  async pickVersion(): Promise<void> {
    await this.pickByLabel("Version");
  }

  async pickTrainDataset(): Promise<void> {
    await this.pickByLabel("Train dataset");
  }

  async pickTestDataset(): Promise<void> {
    await this.pickByLabel("Test dataset");
  }

  submitButton() {
    return this.page.getByRole("button", { name: /submit job/i });
  }

  async submit(): Promise<void> {
    await this.submitButton().click();
  }
}
