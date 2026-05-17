import { test, expect } from "@playwright/test";
import { login } from "../helpers";

test.describe("mobile sidebar drawer", () => {
  test("hamburger opens the drawer; tapping a nav item closes it and navigates", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/detectors");

    // The desktop sidebar is hidden on mobile; the hamburger trigger sits in
    // TopBar with the SidebarTrigger Radix-Slot from shadcn/ui.
    const trigger = page.getByRole("button", { name: /toggle sidebar|menu/i });
    await expect(trigger).toBeVisible();

    // Drawer is closed initially. Click to open.
    await trigger.click();

    // The drawer is rendered as a Radix Dialog (shadcn `<Sheet>`); scope
    // assertions to the drawer container so we don't accidentally match
    // breadcrumb / TopBar links with the same accessible name after navigation.
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    const datasetsLink = drawer.getByRole("link", {
      name: /datasets|資料集/i,
    });
    await expect(datasetsLink).toBeVisible();

    await datasetsLink.click();
    await page.waitForURL(/\/datasets/);
    expect(page.url()).toMatch(/\/datasets/);

    // After navigation the drawer should auto-close.
    await expect(drawer).not.toBeVisible();
  });

  test("ESC closes the open drawer", async ({ page }) => {
    await login(page);
    await page.goto("/detectors");

    const trigger = page.getByRole("button", { name: /toggle sidebar|menu/i });
    await trigger.click();

    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible();
  });

  test("drawer shows the admin link for admin users", async ({ page }) => {
    // Spec §5 PR-4: "admin link only for admin users". `helpers.login()` runs
    // through AUTH_DEV_MODE which seeds an admin user, so the positive case is
    // testable; a negative-persona test would require a second AUTH_DEV_MODE
    // identity and is tracked as a follow-up.
    await login(page);
    await page.goto("/detectors");

    const trigger = page.getByRole("button", { name: /toggle sidebar|menu/i });
    await trigger.click();

    const drawer = page.getByRole("dialog");
    // Anchor to the exact nav label so we don't also match the profile link
    // ("admin@dev.local"); see role-based-visibility.spec.ts for the
    // strict-mode failure that motivated this pattern.
    const adminLink = drawer.getByRole("link", { name: /^(admin|管理)$/i });
    await expect(adminLink).toBeVisible();
  });
});
