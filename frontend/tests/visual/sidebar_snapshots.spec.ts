import { test, expect } from "@playwright/test";

import { loginAs } from "../e2e/helpers";

/**
 * §10 #30 (D2.7) — Sidebar visual snapshot, admin + developer personas.
 */
test.skip("sidebar (admin persona) renders pixel-stable", async ({ page }) => {
  await loginAs(page, "admin");
  await page.goto("/");
  const sidebar = page.locator('[data-sidebar="sidebar"]').first();
  await expect(sidebar).toHaveScreenshot("sidebar-admin.png", {
    animations: "disabled",
  });
});

test.skip("sidebar (developer persona) renders pixel-stable", async ({
  page,
}) => {
  await loginAs(page, "developer");
  await page.goto("/");
  const sidebar = page.locator('[data-sidebar="sidebar"]').first();
  await expect(sidebar).toHaveScreenshot("sidebar-developer.png", {
    animations: "disabled",
  });
});
