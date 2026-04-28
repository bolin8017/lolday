/**
 * Phase 13a A1 — ManifestView null-state fallback tests.
 *
 * Opt-in: set `MANIFEST_E2E=1`.
 *
 * These tests require seeding detector + version records via the backend API.
 * The fixtures `seedLegacyVersion` / `seedActiveVersion` are not yet
 * implemented because this repo has no HTTP-based test-seeding infrastructure
 * (no test DB, no admin seed endpoint) — all existing e2e tests drive the
 * deployed cluster using pre-existing data.
 *
 * TODO(fixture): implement seedLegacyVersion / seedActiveVersion when the
 * project adds a test-seeding API (e.g. POST /api/v1/admin/seed or a
 * dedicated test backend with --reset-db flag). The fixture should:
 *   1. POST /api/v1/detectors  → creates a detector, returns { id }
 *   2. POST /api/v1/detectors/{id}/versions  → creates a version record
 *      with manifest=null  (for seedLegacyVersion) or manifest={...} (for
 *      seedActiveVersion), bypassing the normal build pipeline.
 *   3. Return { id } so the test can navigate to /detectors/{id}.
 *
 * Until that infrastructure exists the tests are skipped; the production
 * code change (ManifestView null fallback) is implemented unconditionally.
 *
 * TODO(fixture-design): When implementing seedLegacyVersion / seedActiveVersion,
 * decide between two approaches and apply consistently across all e2e specs:
 *   (a) Playwright fixture injection via test.extend (matches the writing-plans
 *       template's example signature: `async ({ page, seedLegacyVersion }) => ...`).
 *   (b) Module-level async helper functions (current stub pattern in this file).
 * Other phase 13a/b specs (e.g., the upcoming detectors delete tests) should follow
 * the same choice. See plan Task 1.2 + reviewer feedback on commit 5b6ed83.
 */
import { test, expect, request } from "@playwright/test";
import { login } from "./helpers";

const ENABLED = process.env.MANIFEST_E2E === "1";
const API_BASE = process.env.E2E_API_BASE ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Seed helpers
// ---------------------------------------------------------------------------

/**
 * TODO(fixture): POST seed data for a detector version whose manifest is NULL
 * (simulates a version built before maldet 1.1 / Phase 11e).
 */
async function seedLegacyVersion(
  _apiContext: Awaited<ReturnType<typeof request.newContext>>,
  _opts: { name: string; tag: string },
): Promise<{ id: string }> {
  // TODO(fixture): replace this stub with real API calls once a test-seeding
  // endpoint exists (see module-level comment above).
  throw new Error(
    "seedLegacyVersion is not yet implemented — needs a backend seed API. " +
    "Set MANIFEST_E2E=1 only after implementing the fixture.",
  );
}

/**
 * TODO(fixture): POST seed data for a detector version whose manifest is a
 * non-null object (simulates a version built with maldet >= 1.1).
 */
async function seedActiveVersion(
  _apiContext: Awaited<ReturnType<typeof request.newContext>>,
  _opts: { name: string; tag: string },
): Promise<{ id: string }> {
  // TODO(fixture): replace this stub with real API calls once a test-seeding
  // endpoint exists (see module-level comment above).
  throw new Error(
    "seedActiveVersion is not yet implemented — needs a backend seed API. " +
    "Set MANIFEST_E2E=1 only after implementing the fixture.",
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("View manifest button opens Sheet with fallback for legacy version", async ({ page }) => {
  test.skip(!ENABLED, "set MANIFEST_E2E=1 to enable (requires seed API)");
  test.setTimeout(30_000);
  await login(page);

  // TODO(fixture-cleanup): once fixtures are implemented, dispose this apiContext
  // (e.g., via test.afterEach or fixture teardown) so HTTP sessions are released
  // even on test failure.
  const apiContext = await request.newContext({ baseURL: API_BASE });
  const detector = await seedLegacyVersion(apiContext, { name: "legacy-det", tag: "v0.1.0" });

  await page.goto(`/detectors/${detector.id}`);
  await page.getByRole("tab", { name: /versions/i }).click();
  await page.getByRole("button", { name: /view manifest/i }).first().click();

  // Sheet should be visible
  const sheet = page.getByRole("dialog");
  await expect(sheet).toBeVisible();

  // Fallback text for null manifest
  await expect(sheet.getByText(/legacy build/i)).toBeVisible();
  await expect(sheet.getByText(/rebuild this version/i)).toBeVisible();
});

test("View manifest button opens Sheet with manifest tree for phase11e+ version", async ({ page }) => {
  test.skip(!ENABLED, "set MANIFEST_E2E=1 to enable (requires seed API)");
  test.setTimeout(30_000);
  await login(page);

  // TODO(fixture-cleanup): once fixtures are implemented, dispose this apiContext
  // (e.g., via test.afterEach or fixture teardown) so HTTP sessions are released
  // even on test failure.
  const apiContext = await request.newContext({ baseURL: API_BASE });
  const detector = await seedActiveVersion(apiContext, { name: "modern-det", tag: "v3.0.0" });

  await page.goto(`/detectors/${detector.id}`);
  await page.getByRole("tab", { name: /versions/i }).click();
  await page.getByRole("button", { name: /view manifest/i }).first().click();

  const sheet = page.getByRole("dialog");
  await expect(sheet).toBeVisible();
  // Manifest content (from JSON tree) — at least the detector name should appear
  await expect(sheet.getByText("modern-det")).toBeVisible();
});
