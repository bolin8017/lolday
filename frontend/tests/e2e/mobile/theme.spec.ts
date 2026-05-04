import { test, expect } from "@playwright/test";
import { login } from "../helpers";

test.describe("mobile theme", () => {
  test("switching to dark persists across reload", async ({ page }) => {
    await login(page);
    await page.goto("/detectors");

    const themeButton = page.getByRole("button", {
      name: /toggle theme|切換主題/i,
    });
    await themeButton.click();

    const darkOption = page.getByRole("menuitem", { name: /^dark$|^深色$/i });
    await darkOption.click();

    await expect(page.locator("html")).toHaveClass(/(?:^|\s)dark(?:\s|$)/);

    await page.reload();
    await expect(page.locator("html")).toHaveClass(/(?:^|\s)dark(?:\s|$)/);

    // Cleanup — leave the page in system mode so the next worker starts
    // from a known state.
    await themeButton.click();
    const systemOption = page.getByRole("menuitem", {
      name: /^system$|^跟隨系統$/i,
    });
    await systemOption.click();
  });

  test("system mode follows OS prefers-color-scheme live", async ({ page }) => {
    // Spec §5 PR-4: "System mode follows OS preference". Use Playwright's
    // emulateMedia to flip the simulated OS theme and assert <html> picks up
    // the matching class without a reload — the ThemeProvider attaches a
    // matchMedia change listener exactly for this case.
    await login(page);
    await page.goto("/detectors");

    // Ensure we start in system mode.
    const themeButton = page.getByRole("button", {
      name: /toggle theme|切換主題/i,
    });
    await themeButton.click();
    const systemOption = page.getByRole("menuitem", {
      name: /^system$|^跟隨系統$/i,
    });
    await systemOption.click();

    // Force OS = dark.
    await page.emulateMedia({ colorScheme: "dark" });
    await expect(page.locator("html")).toHaveClass(/(?:^|\s)dark(?:\s|$)/);

    // Force OS = light.
    await page.emulateMedia({ colorScheme: "light" });
    await expect(page.locator("html")).toHaveClass(/(?:^|\s)light(?:\s|$)/);
  });
});
