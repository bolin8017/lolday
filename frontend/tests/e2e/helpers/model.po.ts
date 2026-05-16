/**
 * D3.1 — ModelPage page object model.
 *
 * Used by:
 *   - Task 12 e2e/models/transfer-and-delete.spec.ts
 *
 * Selectors mirror frontend/src/routes/_authed.models.$owner.$name.tsx
 * (the transfer + delete dialogs are rendered via shadcn Dialog with
 * accessible names "Transfer ownership" / "Delete model").
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

  /**
   * Click "Transfer ownership", type the new owner's email into the
   * confirmation dialog, and confirm.
   */
  async transferTo(newOwnerEmail: string): Promise<void> {
    await this.page
      .getByRole("button", { name: /transfer ownership/i })
      .click();
    await this.page
      .getByRole("dialog", { name: /transfer ownership/i })
      .getByRole("textbox")
      .fill(newOwnerEmail);
    await this.page
      .getByRole("dialog", { name: /transfer ownership/i })
      .getByRole("button", { name: /^transfer$/i })
      .click();
  }

  /** Click "Delete model" and confirm in the dialog. */
  async deleteModel(): Promise<void> {
    await this.page.getByRole("button", { name: /delete model/i }).click();
    await this.page
      .getByRole("dialog", { name: /delete model/i })
      .getByRole("button", { name: /^delete$/i })
      .click();
  }

  /** Row in the model list. */
  row(name: string): Locator {
    return this.page.getByRole("row", { name: new RegExp(name, "i") });
  }
}
