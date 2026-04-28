/**
 * Phase 13a A1 / A4 — ManifestView null-state fallback + Delete detector/version UX tests.
 *
 * Opt-in: set `MANIFEST_E2E=1` to enable ALL detector e2e tests (manifest view + delete).
 *
 * These tests require seeding detector + version records via the backend API.
 * The fixtures `seedLegacyVersion` / `seedActiveVersion` / `seedDetector` /
 * `seedDetectorWithVersion` / `seedDetectorWithRunningJob` are not yet
 * implemented because this repo has no HTTP-based test-seeding infrastructure
 * (no test DB, no admin seed endpoint) — all existing e2e tests drive the
 * deployed cluster using pre-existing data.
 *
 * TODO(fixture): implement all seed fixtures when the project adds a test-seeding
 * API (e.g. POST /api/v1/admin/seed or a dedicated test backend with --reset-db flag).
 * Each fixture should:
 *   1. POST /api/v1/detectors  → creates a detector, returns { id, name }
 *   2. POST /api/v1/detectors/{id}/versions  → creates a version record
 *   3. For seedDetectorWithRunningJob, POST /api/v1/detectors/{id}/jobs with
 *      status=RUNNING to create an in-flight job that blocks deletion.
 *   4. Return { id, name, [tag, detectorId, ...] } as needed by the test.
 *
 * Until that infrastructure exists the tests are skipped; the production
 * code changes (ManifestView null fallback, delete UX) are implemented unconditionally.
 *
 * TODO(fixture-design): When implementing all seed fixtures, decide between two
 * approaches and apply consistently across all e2e specs:
 *   (a) Playwright fixture injection via test.extend (matches the writing-plans
 *       template's example signature: `async ({ page, seedDetector }) => ...`).
 *   (b) Module-level async helper functions (current stub pattern in this file).
 * All phase 13a/b specs should follow the same choice. See plan Task 1.2 + A4 +
 * reviewer feedback on commit 5b6ed83.
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

/**
 * TODO(fixture): POST seed data for a detector (Phase 13a A4).
 * Should create a detector and return { id, name }.
 */
async function seedDetector(
  _apiContext: Awaited<ReturnType<typeof request.newContext>>,
  _opts: { name: string },
): Promise<{ id: string; name: string }> {
  // TODO(fixture): replace this stub with real API calls once a test-seeding
  // endpoint exists (see module-level comment above).
  throw new Error(
    "seedDetector is not yet implemented — needs a backend seed API. " +
    "Set MANIFEST_E2E=1 only after implementing the fixture. " +
    "POST /api/v1/detectors with { name, ... } then return { id, name }.",
  );
}

/**
 * TODO(fixture): POST seed data for a detector + version (Phase 13a A4).
 * Should create a detector and a version record, returning { detectorId, tag }.
 */
async function seedDetectorWithVersion(
  _apiContext: Awaited<ReturnType<typeof request.newContext>>,
  _opts: { name: string; tag: string },
): Promise<{ detectorId: string; tag: string }> {
  // TODO(fixture): replace this stub with real API calls once a test-seeding
  // endpoint exists (see module-level comment above).
  throw new Error(
    "seedDetectorWithVersion is not yet implemented — needs a backend seed API. " +
    "Set MANIFEST_E2E=1 only after implementing the fixture. " +
    "POST /api/v1/detectors and POST /api/v1/detectors/{id}/versions, " +
    "then return { detectorId, tag }.",
  );
}

/**
 * TODO(fixture): POST seed data for a detector with a running job (Phase 13a A4).
 * Should create a detector and an in-flight job, returning { detectorId, name }.
 * Used to test that deletion is blocked when jobs are running.
 */
async function seedDetectorWithRunningJob(
  _apiContext: Awaited<ReturnType<typeof request.newContext>>,
  _opts: { name: string },
): Promise<{ detectorId: string; name: string }> {
  // TODO(fixture): replace this stub with real API calls once a test-seeding
  // endpoint exists (see module-level comment above).
  throw new Error(
    "seedDetectorWithRunningJob is not yet implemented — needs a backend seed API. " +
    "Set MANIFEST_E2E=1 only after implementing the fixture. " +
    "POST /api/v1/detectors, then POST /api/v1/detectors/{id}/jobs with status=RUNNING, " +
    "then return { detectorId, name }.",
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

// ---------------------------------------------------------------------------
// Phase 13a A4 — Delete detector / version tests
// ---------------------------------------------------------------------------

test.describe("Delete detector / version", () => {
  test("delete detector happy path", async ({ page }) => {
    test.skip(!ENABLED, "set MANIFEST_E2E=1 to enable (requires seed API)");
    test.setTimeout(30_000);
    await login(page);

    // TODO(fixture-cleanup): once fixtures are implemented, dispose this apiContext
    // (e.g., via test.afterEach or fixture teardown) so HTTP sessions are released
    // even on test failure.
    const apiContext = await request.newContext({ baseURL: API_BASE });
    const { id, name } = await seedDetector(apiContext, { name: "to-delete" });

    await page.goto(`/detectors/${id}`);
    await page.getByRole("button", { name: /^Delete$/ }).first().click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const deleteBtn = dialog.getByRole("button", { name: /^Delete$/ });
    await expect(deleteBtn).toBeDisabled();

    await dialog.getByRole("textbox").fill("wrong-name");
    await expect(deleteBtn).toBeDisabled();

    await dialog.getByRole("textbox").fill(name);
    await expect(deleteBtn).toBeEnabled();
    await deleteBtn.click();

    await expect(page).toHaveURL("/detectors");
    await expect(page.getByText(name)).not.toBeVisible();
  });

  test("delete version happy path", async ({ page }) => {
    test.skip(!ENABLED, "set MANIFEST_E2E=1 to enable (requires seed API)");
    test.setTimeout(30_000);
    await login(page);

    // TODO(fixture-cleanup): once fixtures are implemented, dispose this apiContext
    // (e.g., via test.afterEach or fixture teardown) so HTTP sessions are released
    // even on test failure.
    const apiContext = await request.newContext({ baseURL: API_BASE });
    const { detectorId, tag } = await seedDetectorWithVersion(apiContext, {
      name: "rfdet",
      tag: "v1.0.0",
    });

    await page.goto(`/detectors/${detectorId}`);
    await page.getByRole("tab", { name: /versions/i }).click();
    await page.getByRole("button", { name: /^Delete$/ }).click();

    const dialog = page.getByRole("dialog");
    await dialog.getByRole("textbox").fill(tag);
    await dialog.getByRole("button", { name: /^Delete$/ }).click();

    // Version disappears from list
    await expect(page.getByRole("cell", { name: tag })).not.toBeVisible();
  });

  test("delete blocked by in-flight job", async ({ page }) => {
    test.skip(!ENABLED, "set MANIFEST_E2E=1 to enable (requires seed API)");
    test.setTimeout(30_000);
    await login(page);

    // TODO(fixture-cleanup): once fixtures are implemented, dispose this apiContext
    // (e.g., via test.afterEach or fixture teardown) so HTTP sessions are released
    // even on test failure.
    const apiContext = await request.newContext({ baseURL: API_BASE });
    const { detectorId, name } = await seedDetectorWithRunningJob(apiContext, {
      name: "blocked",
    });

    await page.goto(`/detectors/${detectorId}`);
    await page.getByRole("button", { name: /^Delete$/ }).first().click();

    const dialog = page.getByRole("dialog");
    await dialog.getByRole("textbox").fill(name);
    await dialog.getByRole("button", { name: /^Delete$/ }).click();

    // Dialog stays open with error banner
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/cancel running jobs/i)).toBeVisible();
    await expect(page).toHaveURL(`/detectors/${detectorId}`); // didn't navigate
  });
});
