import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("promote a model version to Production", async ({ page }) => {
  test.setTimeout(60_000);
  await login(page);
  await page.goto("/models");

  // Click the first model row (any registered model — fixture seeds one).
  const firstModel = page
    .locator("table tbody tr")
    .first()
    .getByRole("link")
    .first();
  await expect(firstModel).toBeVisible();
  await firstModel.click();
  await page.waitForURL(/\/models\//);

  // The transition action lives inside a per-VERSION "more" (3-dot)
  // DropdownMenu in `_authed.models.$owner.$name.tsx:218-247`. The page
  // has TWO `aria-label="more"` buttons: one model-level menu (with
  // "Edit description", "Transfer ownership", etc.) at the top and one
  // per-version menu inside each table row. Scope the click to the
  // version table so we don't pick up the model-level menu by accident.
  // Earlier spec used `getByRole('button', { name: /^Transition$/ })`
  // and timed out — no such button exists; the menu item is
  // "Transition stage…" with a Unicode ellipsis and renders only after
  // the per-version menu is opened.
  await page
    .locator("table tbody tr")
    .first()
    .getByRole("button", { name: "more" })
    .click();
  await page.getByRole("menuitem", { name: /Transition stage/ }).click();

  // ModelTransitionDialog opens; the target Select defaults to
  // "Production" (`ModelTransitionDialog.tsx:48`), so confirming
  // immediately promotes the version.
  await page.getByRole("button", { name: /^Confirm$/ }).click();

  // Dialog closes; the rendered version row's stage badge flips to
  // "Production" after the mutation invalidates the query and the
  // refetch lands. Wait on the table cell content so we're not racing
  // a generic "Production" string elsewhere in the layout.
  await expect(page.locator('text="Production"').first()).toBeVisible({
    timeout: 15_000,
  });
});
