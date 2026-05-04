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

    // After opening, the drawer (vaul portal) renders nav links.
    const datasetsLink = page.getByRole("link", { name: /datasets|資料集/i });
    await expect(datasetsLink).toBeVisible();

    await datasetsLink.click();
    await page.waitForURL(/\/datasets/);
    expect(page.url()).toMatch(/\/datasets/);

    // After navigation the drawer should auto-close.
    await expect(datasetsLink).not.toBeVisible();
  });

  test("ESC closes the open drawer", async ({ page }) => {
    await login(page);
    await page.goto("/detectors");

    const trigger = page.getByRole("button", { name: /toggle sidebar|menu/i });
    await trigger.click();

    const datasetsLink = page.getByRole("link", { name: /datasets|資料集/i });
    await expect(datasetsLink).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(datasetsLink).not.toBeVisible();
  });
});
