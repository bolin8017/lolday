/**
 * D3.1 — DetectorPage page object model.
 *
 * Used by:
 *   - Task 11 e2e/detectors/build-and-list.spec.ts
 *   - Task 29 visual/sidebar_snapshots.spec.ts (navigation entry point)
 *
 * Selectors mirror frontend/src/routes/_authed.detectors.* and the
 * detector detail view's "Trigger build" Dialog.
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
   * Click the "Versions" tab on the detail page. The detail page opens
   * on the "Overview" tab by default, so callers that want to assert on
   * the version table must switch tabs first.
   */
  async openVersionsTab(): Promise<void> {
    await this.page.getByRole("tab", { name: /versions/i }).click();
  }

  /**
   * Click the "Builds" tab on the detail page. Caller must already be
   * at /detectors/{id}.
   */
  async openBuildsTab(): Promise<void> {
    await this.page.getByRole("tab", { name: /builds/i }).click();
  }

  /**
   * Open the build Dialog by clicking "+ Trigger build" on the Builds
   * tab. Does NOT submit — call `confirmBuildDialog(tag)` afterwards
   * to pick a tag and POST `/api/v1/detectors/{id}/builds`.
   */
  async triggerBuild(): Promise<void> {
    await this.page.getByRole("button", { name: /trigger build/i }).click();
  }

  /**
   * Inside the open trigger-build Dialog: select `tag` from the
   * "Git tag" Select and click "Build" to fire the POST. The Build
   * button is disabled until a tag is picked
   * (`_authed.detectors.$id.tsx:308`), so this is the only path that
   * actually creates a build.
   */
  async confirmBuildDialog(tag: string): Promise<void> {
    const dialog = this.page.getByRole("dialog", { name: /trigger build/i });
    await dialog.getByRole("combobox", { name: /git tag/i }).click();
    await this.page.getByRole("option", { name: new RegExp(tag) }).click();
    await dialog.getByRole("button", { name: /^build$/i }).click();
  }

  /**
   * Row in the version table on the detail page. Pass the version's
   * `git_tag` to scope; tests can chain `.click()` / `.getByRole(...)`.
   * Caller must have already opened the Versions tab.
   */
  versionRow(gitTag: string): Locator {
    return this.page.getByRole("row", { name: new RegExp(gitTag, "i") });
  }
}
