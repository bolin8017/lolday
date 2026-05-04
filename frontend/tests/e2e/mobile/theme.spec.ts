import { test, expect } from "@playwright/test";
import { login } from "../helpers";

test("theme: switching to dark persists across reload", async ({ page }) => {
  await login(page);
  await page.goto("/detectors");

  // Open the theme toggle dropdown.
  const themeButton = page.getByRole("button", {
    name: /toggle theme|切換主題/i,
  });
  await themeButton.click();

  // Click "Dark" / "深色"
  const darkOption = page.getByRole("menuitem", { name: /^dark$|^深色$/i });
  await darkOption.click();

  // After click, <html> should carry class="dark"
  await expect(page.locator("html")).toHaveClass(/(?:^|\s)dark(?:\s|$)/);

  // Reload and verify the class persists (localStorage)
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/(?:^|\s)dark(?:\s|$)/);

  // Cleanup: reset to system to leave the test session in a known state
  await themeButton.click();
  const systemOption = page.getByRole("menuitem", {
    name: /^system$|^跟隨系統$/i,
  });
  await systemOption.click();
});
