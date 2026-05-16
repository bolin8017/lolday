/**
 * D3.1 — DetectorPage page object model.
 *
 * Used by:
 *   - Task 11 e2e/detectors/build-and-list.spec.ts
 *   - Task 29 visual/sidebar_snapshots.spec.ts (navigation entry point)
 *
 * Selectors mirror frontend/src/routes/_authed.detectors.* and the
 * detector detail view's "Trigger build" button.
 */
import type { Locator, Page } from "@playwright/test";

export class DetectorPage {
  constructor(private readonly page: Page) {}

  async gotoList(): Promise<void> {
    await this.page.goto("/detectors");
  }

  async gotoDetail(detectorId: string): Promise<void> {
    await this.page.goto(`/detectors/${detectorId}`);
  }

  async gotoNew(): Promise<void> {
    await this.page.goto("/detectors/new");
  }

  /**
   * Trigger a build on the detail page. Caller must already be at
   * /detectors/{id}.
   */
  async triggerBuild(): Promise<void> {
    await this.page.getByRole("button", { name: /trigger build/i }).click();
  }

  /**
   * Row in the version table on the detail page. Pass the version's
   * `git_tag` to scope; tests can chain `.click()` / `.getByRole(...)`.
   */
  versionRow(gitTag: string): Locator {
    return this.page.getByRole("row", { name: new RegExp(gitTag, "i") });
  }
}
