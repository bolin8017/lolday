/**
 * Phase 11e production smoke test.
 *
 * Opt-in: set `PHASE11E_VERIFY=1` and source `.lolday-secrets.env` (CF_ACCESS_CLIENT_ID/SECRET live there as of 2026-04-29).
 * Drives the full RJSF → submit → reconciler → list-page tile flow against
 * the deployed cluster. Skipped by default so the regular CI run doesn't
 * need a deployed stack.
 */
import { test, expect } from "@playwright/test";

const ENABLED = process.env.PHASE11E_VERIFY === "1";
const DETECTOR_NAME = process.env.PHASE11E_DETECTOR ?? "elfrfdet";
const DETECTOR_TAG = process.env.PHASE11E_DETECTOR_TAG ?? "v3.0.0";

test.use({
  baseURL: "https://lolday.connlabai.com",
  ignoreHTTPSErrors: true,
  extraHTTPHeaders: {
    "CF-Access-Client-Id": process.env.CF_ACCESS_CLIENT_ID ?? "",
    "CF-Access-Client-Secret": process.env.CF_ACCESS_CLIENT_SECRET ?? "",
  },
  launchOptions: { args: [] },
});

test("phase 11e — RJSF renders for v3.0.0 detector on /jobs/new", async ({
  page,
}) => {
  test.skip(!ENABLED, "set PHASE11E_VERIFY=1 to enable");
  test.setTimeout(120_000);

  await page.goto("/jobs/new", { waitUntil: "domcontentloaded" });

  // Pick detector + version. Selectors match shadcn/ui Combobox structure.
  await page
    .getByText(/^Detector$/)
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option", { name: new RegExp(DETECTOR_NAME) }).click();
  await page
    .getByText(/^Version$/)
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option", { name: new RegExp(DETECTOR_TAG) }).click();

  // Confirm RJSF rendered: look for a known field name from elfrfdet TrainConfig
  // (n_estimators) or elfcnndet TrainConfig (epochs).
  await expect(page.getByLabel(/n_estimators|epochs/i)).toBeVisible({
    timeout: 10_000,
  });

  // Smoke only — not actually submitting (would consume cluster resources).
  await page.screenshot({ path: "/tmp/phase11e-rjsf.png" });
});

test("phase 11e — list page renders Final metrics tile column", async ({
  page,
}) => {
  test.skip(!ENABLED, "set PHASE11E_VERIFY=1 to enable");

  await page.goto("/jobs", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("Final metrics")).toBeVisible({
    timeout: 10_000,
  });
});

test("phase 11e — detector page View manifest sheet opens with json", async ({
  page,
}) => {
  test.skip(!ENABLED, "set PHASE11E_VERIFY=1 to enable");

  await page.goto("/detectors", { waitUntil: "domcontentloaded" });
  // Click the row matching our detector name
  await page
    .getByRole("link", { name: new RegExp(DETECTOR_NAME, "i") })
    .first()
    .click();
  await page.waitForURL(/\/detectors\/[0-9a-f-]+/);
  // Switch to Versions tab
  await page.getByRole("tab", { name: /versions/i }).click();
  // Click View manifest on the v3.0.0 row
  const row = page.getByRole("row", { name: new RegExp(DETECTOR_TAG) });
  await row.getByRole("button", { name: /view manifest/i }).click();
  // The Sheet should show the manifest JSON — match on a known key
  await expect(page.getByText(/"params_schema"/)).toBeVisible({
    timeout: 10_000,
  });
});

test("phase 11e — newly trained model version defaults to private visibility", async ({
  page,
}) => {
  test.skip(!ENABLED, "set PHASE11E_VERIFY=1 to enable");

  // Navigate to the Models list, filter to "Mine" to show own models.
  await page.goto("/models", { waitUntil: "domcontentloaded" });
  await page
    .getByRole("combobox")
    .filter({ hasText: /All|Mine|Public/i })
    .click();
  await page.getByRole("option", { name: /Mine/i }).click();

  // Find the row whose model name matches DETECTOR_NAME and open its detail page.
  const detectorLink = page.getByRole("link", {
    name: new RegExp(DETECTOR_NAME, "i"),
  });
  await expect(detectorLink.first()).toBeVisible({ timeout: 10_000 });
  await detectorLink.first().click();
  await page.waitForURL(/\/models\/[^/]+\/[^/]+/);

  // Versions table must be present
  await expect(page.getByRole("heading", { name: /Versions/i })).toBeVisible({
    timeout: 10_000,
  });

  // The most recently registered version (first row) should carry a Lock badge
  // (aria-label="lock") — confirming the default visibility is private.
  await expect(
    page.locator("table tbody tr").first().locator('[aria-label="lock"]'),
  ).toBeVisible({ timeout: 10_000 });
});
