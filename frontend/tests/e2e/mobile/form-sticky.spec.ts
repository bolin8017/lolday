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

  // Verify the button is positioned within the viewport rect.
  const viewport = page.viewportSize();
  const box = await submit.boundingBox();
  expect(viewport).toBeTruthy();
  expect(box).toBeTruthy();
  if (viewport && box) {
    // The submit button's bottom edge should be at or near the viewport bottom.
    // Allow 5 px slack for safe-area-inset-bottom rounding.
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 5);
    // It should also be at least partially within the viewport.
    expect(box.y).toBeLessThan(viewport.height);
  }
});
