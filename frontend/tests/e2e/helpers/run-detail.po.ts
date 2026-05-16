/**
 * D3.1 — RunDetailPage page object model.
 *
 * Used by:
 *   - Task 10 e2e/jobs/full-lifecycle.spec.ts (assert SUCCEEDED status on
 *     the run detail page after job completion)
 *   - Task 15 visual/i18n_cross_locale.spec.ts (snapshot the page in
 *     both en + zh-TW)
 *
 * Selectors mirror frontend/src/routes/_authed.runs.$expId.$runId.tsx.
 */
import { expect, type Locator, type Page } from "@playwright/test";

export class RunDetailPage {
  constructor(private readonly page: Page) {}

  async goto(expId: string, runId: string): Promise<void> {
    await this.page.goto(`/runs/${expId}/${runId}`);
  }

  /**
   * Returns a locator to the "Open in MLflow" anchor (rendered by
   * OpenInMlflowButton). The href points at the operator-side MLflow UI;
   * tests can assert presence without following the link.
   */
  openInMlflow(): Locator {
    return this.page.getByRole("link", { name: /open in mlflow/i });
  }

  /** Row in the per-metric table, scoped by metric key. */
  metricRow(key: string): Locator {
    return this.page.getByRole("row", { name: new RegExp(key, "i") });
  }

  /**
   * Assert the run page renders a StatusBadge with the given status.
   * Status text is i18n-translated; the badge data-testid carries the
   * raw enum value for stable assertion regardless of locale.
   */
  async expectStatus(
    status: "succeeded" | "failed" | "running",
  ): Promise<void> {
    await expect(this.page.getByTestId(`status-badge-${status}`)).toBeVisible();
  }
}
