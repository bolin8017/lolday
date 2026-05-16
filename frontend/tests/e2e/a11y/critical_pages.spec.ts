import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";

/**
 * D3.6 — a11y baseline (WCAG 2.1 AA).
 *
 * Critical pages = pages every signed-in user touches. Failure mode:
 * any AxeBuilder violation fails the spec with the detailed list.
 *
 * Findings deferred via `.disableRules(["..."])` must carry a same-line
 * reason + a tracking issue.
 */
const CRITICAL_PAGES = [
  { path: "/detectors", name: "detectors-list" },
  { path: "/jobs", name: "jobs-list" },
  { path: "/jobs/new", name: "jobs-new" },
  { path: "/runs", name: "runs-list" },
  { path: "/profile", name: "profile" },
] as const;

test.describe("a11y baseline (axe WCAG 2.1 AA)", () => {
  for (const { path, name } of CRITICAL_PAGES) {
    test(`${name} has no a11y violations`, async ({ page }) => {
      await loginAs(page, "admin");
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      expect(
        results.violations,
        `a11y violations on ${path}:\n${JSON.stringify(results.violations, null, 2)}`,
      ).toEqual([]);
    });
  }
});
