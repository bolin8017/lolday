import { test, expect } from "@playwright/test";

import { loginAs } from "./helpers";
import { JobSubmitPage } from "./helpers/job-submit.po";

/**
 * Admin submits a Train job; the form POSTs, redirects to /jobs/{id},
 * and the new job appears in the list at the QUEUED_BACKEND status.
 *
 * Why the title is no longer "...and see it succeed": the prior version
 * waited up to 8 minutes for `getByText(/succeeded/i)` to appear,
 * relying on a real cluster's reconciler to transition the Volcano vcjob
 * through PREPARING → RUNNING → SUCCEEDED. The Playwright live-stack
 * runs against in-process `StubVolcano` (`app/services/_stubs.py`) which
 * does NOT simulate the lifecycle — newly-created jobs stay at
 * `queued_backend` indefinitely, so the assertion always timed out.
 * That intent (full real-cluster lifecycle) belongs in a cluster-only
 * smoke (`chart-e2e.yml` or future live-cluster Playwright workflow),
 * not here. The stubs-friendly verification covers:
 *
 * 1. Form pickers work against the seeded fixture detector.
 * 2. POST /api/v1/jobs returns 202 with a new job id.
 * 3. The frontend navigates to /jobs/{id} after submit.
 * 4. GET /api/v1/jobs lists the new job (status queued_backend).
 *
 * Selectors also switched from text→parent→combobox to the JobSubmitPage
 * POM's aria-label-based picker. The earlier text-based chain failed
 * because the seeded fixture detector is `elfrfdet`, not `upxelfdet`,
 * and "Detector" appears in multiple elements on the form.
 */
test("developer submits a Train job and the new row appears in the list", async ({
  page,
}) => {
  // Use the `developer` persona instead of `admin` so this spec doesn't
  // share admin's in-flight job budget with `full-lifecycle.spec.ts`
  // (which submits as admin). `JOB_PER_USER_CONCURRENCY=2` (`config.py:73`)
  // + 1 fixture-seeded admin job = 1 remaining admin slot, which
  // full-lifecycle uses; this spec submitting as admin too would push
  // admin to 3-in-flight and trip `concurrency_limit` 429 in whichever
  // ran second. Per-persona budgets are independent.
  await loginAs(page, "developer");

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
  const created = (await submitResp.json()) as { id: string };
  expect(created.id).toBeTruthy();

  await page.waitForURL(/\/jobs\/[0-9a-f-]+/, { timeout: 15_000 });

  // Verify the new job is queryable via the list API (the same surface
  // `/jobs` consumes via `useJobs`). The DOM table columns don't carry
  // the job ID, so the API is the natural anchor.
  const listResp = await page.request.get("/api/v1/jobs");
  expect(listResp.status()).toBe(200);
  const list = (await listResp.json()) as { items: { id: string }[] };
  expect(list.items.some((j) => j.id === created.id)).toBe(true);
});
