import { test, expect } from "@playwright/test";
import { login, seedCreds } from "./helpers";

test("unauthenticated root redirects to /login", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL("**/login");
  await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();
});

test("valid creds reach the authed app", async ({ page }) => {
  await login(page);
  await expect(page).toHaveURL(/\/detectors/);
});

test("invalid creds show error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill(seedCreds().email);
  await page.getByLabel(/password/i).fill("definitely-wrong");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByText(/invalid email or password/i)).toBeVisible();
});
