import { test, expect } from "@playwright/test";
import { login } from "../helpers";

/**
 * Visual regression snapshots for the mobile drawer in both themes.
 *
 * Why these exist: the PR-79 `--sidebar-background` token mismatch made the
 * drawer render transparent in production while the unit suite + the
 * behaviour-only E2E specs (open/close, ARIA roles, navigation) all stayed
 * green. Pixel-level diffs against a baseline are the only assertion class
 * that catches that family of bug.
 *
 * Workflow:
 * - First run on a clean cluster:
 *   `pnpm playwright test --update-snapshots --project=iphone-13-mini visual.spec.ts`
 *   generates `visual.spec.ts-snapshots/*.png`, commit them.
 * - Subsequent runs perform pixel diff against those baselines. If
 *   anti-aliasing flake appears on a different host, raise tolerance via
 *   `{ maxDiffPixelRatio: 0.01 }` on the assertion (avoid raising globally;
 *   per-test gives loud failures elsewhere).
 *
 * The drawer's content is content-stable (5–6 fixed nav links + theme
 * toggle) so screenshot diffs are deterministic across cluster data
 * variation. The page background underneath is NOT included in the
 * screenshot (we snapshot the drawer dialog only) to keep the assertion
 * narrow.
 */

async function openDrawer(page: import("@playwright/test").Page) {
  const trigger = page.getByRole("button", { name: /toggle sidebar|menu/i });
  await trigger.click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  return drawer;
}

async function setTheme(page: import("@playwright/test").Page, label: RegExp) {
  const themeButton = page.getByRole("button", {
    name: /toggle theme|切換主題/i,
  });
  await themeButton.click();
  const option = await page.getByRole("menuitem", { name: label });
  await option.click();
}

test.describe("mobile drawer visual", () => {
  test("drawer open in light theme", async ({ page }) => {
    await login(page);
    await page.goto("/detectors");
    await setTheme(page, /^light$|^淺色$/i);
    const drawer = await openDrawer(page);
    await expect(drawer).toHaveScreenshot("drawer-light.png", {
      animations: "disabled",
    });
  });

  test("drawer open in dark theme", async ({ page }) => {
    await login(page);
    await page.goto("/detectors");
    await setTheme(page, /^dark$|^深色$/i);
    const drawer = await openDrawer(page);
    await expect(drawer).toHaveScreenshot("drawer-dark.png", {
      animations: "disabled",
    });
  });
});
