import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";
import { RunDetailPage } from "../helpers/run-detail.po";

/**
 * D3.7 — mobile run detail.
 *
 * Handles the empty-state path explicitly (no MLflow runs seeded by
 * default) so the spec stays deterministic.
 */
test("mobile: run detail renders + open-in-mlflow tappable when present", async ({
  page,
}) => {
  await loginAs(page, "admin");
  await page.goto("/runs");

  const emptyState = page.getByText(/no runs|尚未/i);
  if (await emptyState.isVisible().catch(() => false)) {
    await expect(emptyState).toBeVisible();
    return;
  }

  const firstRow = page.getByRole("row").nth(1);
  await firstRow.click();
  const runDetail = new RunDetailPage(page);
  await expect(runDetail.openInMlflow()).toBeVisible();
  const box = await runDetail.openInMlflow().boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(40);
});
