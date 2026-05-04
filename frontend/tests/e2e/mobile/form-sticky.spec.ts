import { test, expect } from "@playwright/test";
import { login } from "../helpers";

test("/jobs/new — Submit button visible at viewport bottom without scrolling", async ({
  page,
}) => {
  await login(page);
  await page.goto("/jobs/new");

  // Submit button should be in the DOM (sticky bottom on mobile).
  const submit = page.getByRole("button", { name: /submit job/i });
  await expect(submit).toBeVisible();

  // The page must NOT have auto-scrolled to bring the button into view —
  // sticky positioning should make the bar visible at viewport bottom from
  // the initial render. A non-zero scrollY would mean the test passes for
  // the wrong reason.
  const scrollY = await page.evaluate(() => window.scrollY);
  expect(scrollY).toBe(0);

  // Verify the button is positioned in the BOTTOM region of the viewport
  // (sticky bar). The 5 px slack covers sub-pixel rounding; safe-area inset
  // pushes the button up but never below the viewport.
  const viewport = page.viewportSize();
  const box = await submit.boundingBox();
  expect(viewport).toBeTruthy();
  expect(box).toBeTruthy();
  if (viewport && box) {
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 5);
    // Button must be in the lower 25% of the viewport — anywhere higher
    // suggests the sticky bar is broken (button rendered mid-page).
    expect(box.y).toBeGreaterThan(viewport.height * 0.75);
  }
});
