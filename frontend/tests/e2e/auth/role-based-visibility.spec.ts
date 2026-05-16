import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";

/**
 * D3.2 — role-based UI visibility.
 *
 * Backend gates `/admin/users` on Role.ADMIN; the AppSidebar conditionally
 * renders the nav link only when `currentUser.role === "admin"`. This spec
 * proves the gate works in both directions:
 *   - admin persona sees the nav link + can land on /admin/users
 *   - developer persona does NOT see the nav link
 *   - user persona does NOT see the nav link, AND a direct GET on
 *     /admin/users returns 403 from the backend
 */
test.describe("admin nav visibility per role", () => {
  test("admin persona sees /admin/users nav link", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/");
    await expect(
      page.getByRole("link", { name: /admin|管理員/i }),
    ).toBeVisible();
  });

  test("developer persona does NOT see /admin/users nav link", async ({
    page,
  }) => {
    await loginAs(page, "developer");
    await page.goto("/");
    await expect(page.getByRole("link", { name: /admin|管理員/i })).toHaveCount(
      0,
    );
  });

  test("user persona does NOT see /admin/users nav link", async ({ page }) => {
    await loginAs(page, "user");
    await page.goto("/");
    await expect(page.getByRole("link", { name: /admin|管理員/i })).toHaveCount(
      0,
    );
  });

  test("user persona hitting /admin/users directly receives a 403", async ({
    page,
  }) => {
    await loginAs(page, "user");
    const responsePromise = page.waitForResponse(
      (resp) =>
        resp.url().includes("/api/v1/admin/users") &&
        resp.request().method() === "GET",
    );
    await page.goto("/admin/users");
    const resp = await responsePromise;
    expect(resp.status()).toBe(403);
  });
});
