import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";
import { JobSubmitPage } from "../helpers/job-submit.po";

/**
 * D3.3 — critical user flow: job lifecycle (form → submit → list →
 * detail). Reconciler / Volcano dispatch is NOT exercised here.
 */
test("admin submits a Train job and sees it in the list", async ({ page }) => {
  await loginAs(page, "admin");

  const submit = new JobSubmitPage(page);
  await submit.goto();
  await submit.selectJobType("Train");
  await submit.pickDetector();
  await submit.pickVersion();
  await submit.pickTrainDataset();

  await expect(submit.submitButton()).toBeEnabled();

  const responsePromise = page.waitForResponse(
    (resp) =>
      resp.url().endsWith("/api/v1/jobs") && resp.request().method() === "POST",
  );
  await submit.submit();
  const submitResp = await responsePromise;
  expect(submitResp.status()).toBe(202);
  const created = await submitResp.json();
  expect(created.id).toBeTruthy();

  await page.goto("/jobs");
  // The Jobs list table has columns Type / Status / Submitted / Duration
  // / Final metrics / Priority — NONE of them render the full job ID, so
  // `getByRole('row').filter({ hasText: <uuid> })` never matched the new
  // row. The spec's intent is "after submit, the new job is in the
  // listing"; verify that via the listing API (which the page consumes
  // via `useJobs`) and verify the page itself rendered at least one
  // data row so we know the table actually mounted with data.
  await expect(page.locator("table tbody tr").first()).toBeVisible();
  const listResp = await page.request.get("/api/v1/jobs");
  expect(listResp.status()).toBe(200);
  const list = (await listResp.json()) as { items: { id: string }[] };
  expect(list.items.some((j) => j.id === created.id)).toBe(true);

  const detail = await page.request.get(`/api/v1/jobs/${created.id}`);
  expect(detail.status()).toBe(200);
});
