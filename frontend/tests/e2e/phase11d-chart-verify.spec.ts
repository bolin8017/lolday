/**
 * Phase 11d production smoke test for the live-metric chart.
 *
 * Opt-in: set `PHASE11D_VERIFY=1`, `PHASE11D_JOB_ID=<terminal-job-uuid>`, and
 * source `.lolday-secrets.env` (CF_ACCESS_CLIENT_ID/SECRET live there as of 2026-04-29) before
 * running. CI / local dev runs skip this spec since they don't have prod
 * credentials; skipping it via `test.skip` keeps the default `pnpm test:e2e`
 * green.
 */
import { test, expect } from "@playwright/test";

const ENABLED = process.env.PHASE11D_VERIFY === "1";
const JOB_ID = process.env.PHASE11D_JOB_ID ?? "b4430357-00a8-439c-881e-a45f470363ee";

test.use({
  baseURL: "https://lolday.connlabai.com",
  ignoreHTTPSErrors: true,
  extraHTTPHeaders: {
    "CF-Access-Client-Id": process.env.CF_ACCESS_CLIENT_ID ?? "",
    "CF-Access-Client-Secret": process.env.CF_ACCESS_CLIENT_SECRET ?? "",
  },
  launchOptions: { args: [] },
});

test("phase 11d live-metric chart renders historical events for completed job", async ({
  page,
}) => {
  test.skip(!ENABLED, "set PHASE11D_VERIFY=1 + service-token env to enable");
  test.setTimeout(120_000);

  await page.goto(`/jobs/${JOB_ID}`, { waitUntil: "domcontentloaded" });

  await expect(page.getByText("Live metrics")).toBeVisible({ timeout: 15_000 });

  const lineSeries = page.locator(".recharts-line-curve");
  await expect(lineSeries.first()).toBeVisible({ timeout: 15_000 });
  const lineCount = await lineSeries.count();
  expect(lineCount).toBeGreaterThan(0);

  const trainLossLegend = page.getByText("train_loss");
  await expect(trainLossLegend).toBeVisible({ timeout: 5_000 });

  await page.screenshot({ path: "/tmp/phase11d-chart-verify.png", fullPage: true });
});
