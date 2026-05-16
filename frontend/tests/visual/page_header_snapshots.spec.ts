import { test, expect } from "@playwright/test";

import { loginAs } from "../e2e/helpers";

/**
 * §10 #30 (D2.7) — PageHeader visual snapshot.
 */
test.skip("page header on /detectors renders pixel-stable", async ({
  page,
}) => {
  await loginAs(page, "admin");
  await page.goto("/detectors");
  const header = page.locator('[data-testid="page-header"]').first();
  await expect(header).toHaveScreenshot("page-header-detectors.png", {
    animations: "disabled",
  });
});
