/**
 * D3.1 — ModelPage page object model.
 *
 * Used by:
 *   - Task 12 e2e/models/transfer-and-delete.spec.ts
 *
 * Selectors mirror `_authed.models.$owner.$name.tsx`. Both the transfer
 * and delete actions live inside the model-level "more" DropdownMenu
 * (gated by `isOwnerOrAdmin`); the menu trigger is a `MoreVertical` icon
 * button with `aria-label="more"` inside the page `<header>`. The per-
 * version table rows also expose a `more` button each — scope the
 * trigger via `header` so we don't pick up a version-row menu (the same
 * two-menus-on-page gotcha that bit `model-transition.spec.ts`, see
 * PR #284).
 */
import type { Locator, Page } from "@playwright/test";

export class ModelPage {
  constructor(private readonly page: Page) {}

  async gotoList(): Promise<void> {
    await this.page.goto("/models");
  }

  async gotoDetail(owner: string, name: string): Promise<void> {
    await this.page.goto(`/models/${owner}/${name}`);
  }

  /** Open the model-level "more" DropdownMenu in the page header. */
  private async openHeaderMenu(): Promise<void> {
    await this.page
      .locator("header")
      .getByRole("button", { name: "more" })
      .click();
  }

  /**
   * Open the model-level "more" menu, click "Transfer ownership",
   * fill the new-owner handle, and confirm.
   *
   * @param newOwnerHandle - the destination user's `handle` (slug-form,
   *   e.g. "dev"), NOT their email. The backend looks up
   *   `User.handle == new_owner_handle` (`models_registry.py:548`); an
   *   email here yields a 422 "user not found".
   */
  async transferTo(newOwnerHandle: string): Promise<void> {
    await this.openHeaderMenu();
    await this.page
      .getByRole("menuitem", { name: /transfer ownership/i })
      .click();
    const dialog = this.page.getByRole("dialog", {
      name: /transfer ownership/i,
    });
    // The dialog has two textbox-role inputs (the handle Input and the
    // Optional-comment Textarea). Target the labelled new-owner Input
    // by its id rather than `getByRole('textbox')` (strict-mode
    // ambiguous across both controls).
    await dialog.locator("#new-owner").fill(newOwnerHandle);
    await dialog.getByRole("button", { name: /^transfer$/i }).click();
  }

  /**
   * Open the model-level "more" menu, click "Delete model", type the
   * required `{owner}/{name}` confirmation, and confirm.
   *
   * The Delete button is disabled until the confirmation input matches
   * the model's full name (`DeleteModelDialog.tsx`), so callers must
   * pass the same owner/name pair they navigated to.
   */
  async deleteModel(owner: string, name: string): Promise<void> {
    await this.openHeaderMenu();
    await this.page.getByRole("menuitem", { name: /delete model/i }).click();
    const dialog = this.page.getByRole("dialog", { name: /delete model/i });
    await dialog.getByRole("textbox").fill(`${owner}/${name}`);
    await dialog.getByRole("button", { name: /^delete$/i }).click();
  }

  /** Row in the model list. */
  row(name: string): Locator {
    return this.page.getByRole("row", { name: new RegExp(name, "i") });
  }
}
