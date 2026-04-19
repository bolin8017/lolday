import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("promote a model version to Production", async ({ page }) => {
  test.setTimeout(60_000);
  await login(page);
  await page.goto("/models");
  // Click the first model row (upxelfdet)
  const firstModel = page.locator('table tbody tr').first().getByRole("link").first();
  await expect(firstModel).toBeVisible();
  await firstModel.click();
  await page.waitForURL(/\/models\//);

  // Click the first Transition button (topmost version row)
  const transitionBtn = page.getByRole("button", { name: /^Transition$/ }).first();
  await transitionBtn.click();

  // Dialog opens — the target Select defaults to Production. Just confirm.
  await page.getByRole("button", { name: /^Confirm$/ }).click();

  // Dialog closes; page should still have at least one Production badge after refetch.
  await expect(page.locator('text="Production"').first()).toBeVisible({ timeout: 15_000 });
});
