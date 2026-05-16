import { test, expect } from "@playwright/test";

import { loginAs } from "../e2e/helpers";

/**
 * D3.5 — visual cross-locale snapshot.
 *
 * Catches translation-overflow + themed-state regressions that key-
 * existence checks (Task 14) miss.
 *
 * Workflow:
 *   - Operator first run: pnpm playwright test --update-snapshots tests/visual/i18n_cross_locale.spec.ts
 *     generates baselines under tests/visual/i18n_cross_locale.spec.ts-snapshots/.
 *   - Subsequent runs perform pixel diff.
 *
 * Until baselines are seeded, the specs are marked `.skip` so CI does
 * not report a missing-baseline failure on the first run after this
 * file lands. Operator removes `.skip` after the first
 * `--update-snapshots` pass.
 */
async function setLocale(
  page: import("@playwright/test").Page,
  locale: "en" | "zh-TW",
) {
  await page.addInitScript(
    ([k, v]) => localStorage.setItem(k, v),
    ["i18nextLng", locale],
  );
}

test.describe("cross-locale visual snapshots", () => {
  for (const locale of ["en", "zh-TW"] as const) {
    test.skip(`/detectors list — ${locale}`, async ({ page }) => {
      await setLocale(page, locale);
      await loginAs(page, "admin");
      await page.goto("/detectors");
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveScreenshot(`detectors-list-${locale}.png`, {
        animations: "disabled",
        fullPage: true,
      });
    });

    test.skip(`/profile — ${locale}`, async ({ page }) => {
      await setLocale(page, locale);
      await loginAs(page, "admin");
      await page.goto("/profile");
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveScreenshot(`profile-${locale}.png`, {
        animations: "disabled",
        fullPage: true,
      });
    });
  }
});
