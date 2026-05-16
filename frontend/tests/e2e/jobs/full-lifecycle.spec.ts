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
  await expect(
    page.getByRole("row").filter({ hasText: created.id }),
  ).toBeVisible();

  const detail = await page.request.get(`/api/v1/jobs/${created.id}`);
  expect(detail.status()).toBe(200);
});
