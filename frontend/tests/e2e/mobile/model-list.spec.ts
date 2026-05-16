import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";

/**
 * D3.7 — mobile model list.
 *
 * Validates the model-list page renders + first row is tappable on
 * iPhone 13 Mini. Uses a row locator that doesn't depend on a
 * data-testid (works with the default rendered structure).
 */
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
