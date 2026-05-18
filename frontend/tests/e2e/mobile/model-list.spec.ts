import { test, expect } from "@playwright/test";

import { loginAs, reseedAsAdmin } from "../helpers";

/**
 * D3.7 — mobile model list.
 *
 * Validates the model-list page renders + first row is tappable on
 * iPhone 13 Mini. Uses a row locator that doesn't depend on a
 * data-testid (works with the default rendered structure).
 *
 * Defense-in-depth re-seed: `models/transfer-and-delete.spec.ts` DELETEs
 * the shared seed mid-test. Its `afterEach` re-creates the row, but
 * there's a ms-scale race window where a parallel iphone-13-mini
 * worker can land on /models with no rows. A `beforeEach` re-seed
 * makes this spec self-healing.
 */
test.beforeEach(async ({ browser }) => {
  await reseedAsAdmin(browser);
});

test("mobile: model list renders + first row is tappable", async ({ page }) => {
  await loginAs(page, "admin");
  await page.goto("/models");

  // Use first link to a model detail page (works whether the list
  // renders as cards on mobile or rows on desktop).
  const firstModelLink = page
    .getByRole("link")
    .filter({
      hasText: /fixture-model|elfrfdet/i,
    })
    .first();
  await expect(firstModelLink).toBeVisible();
  await firstModelLink.click();
  await expect(page).toHaveURL(/\/models\/[^/]+\/[^/]+$/);
});
