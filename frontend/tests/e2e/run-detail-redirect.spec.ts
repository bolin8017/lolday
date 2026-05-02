import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("Run Detail deeplink redirects to Job Detail when run has lolday.job_id tag", async ({
  page,
  request,
}) => {
  await login(page);

  // Find any job that has both mlflow_run_id and mlflow_experiment_id.
  // The /api/v1/jobs endpoint returns a paginated list — filter to first match.
  const resp = await request.get("/api/v1/jobs?limit=20");
  expect(resp.ok()).toBe(true);
  const rows = (await resp.json()) as Array<{
    id: string;
    mlflow_run_id: string | null;
    mlflow_experiment_id: string | null;
    status: string;
  }>;
  const job = rows.find((r) => !!r.mlflow_run_id && !!r.mlflow_experiment_id);
  test.skip(
    !job,
    "no job with mlflow ids in /api/v1/jobs?limit=20 — submit a baseline job first",
  );

  // Visit the deeplink — react-router's <Navigate replace> should jump to
  // /jobs/<id> without a stop on the redirect URL.
  await page.goto(`/runs/${job!.mlflow_experiment_id}/${job!.mlflow_run_id}`);

  // The redirect should complete within a few seconds.
  await page.waitForURL(`**/jobs/${job!.id}`, { timeout: 10_000 });
  expect(page.url()).toContain(`/jobs/${job!.id}`);

  // Verify Job Detail rendered (look for the "Job <type>" header pattern).
  await expect(page.locator("h1").first()).toContainText(
    /job|train|evaluate|predict/i,
  );
});
