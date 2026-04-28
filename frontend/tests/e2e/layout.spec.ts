/**
 * Phase 13a A3 — App-shell layout: sidebar bottom block (profile/logout) always visible.
 *
 * Opt-in: set `LAYOUT_E2E=1`.
 *
 * These tests require seeding job/run records so the DataTable is long enough
 * to overflow the viewport. The fixtures `seedManyJobs` / `seedManyRuns` are not
 * yet implemented because this repo has no HTTP-based test-seeding infrastructure
 * (no test DB, no admin seed endpoint) — all existing e2e tests drive the
 * deployed cluster using pre-existing data.
 *
 * TODO(fixture): implement seedManyJobs / seedManyRuns when the project adds a
 * test-seeding API (e.g. POST /api/v1/admin/seed or a dedicated test backend with
 * --reset-db flag). The fixture should:
 *   1. POST /api/v1/admin/seed/jobs  → bulk-creates N job records, returns { ids }
 *   2. POST /api/v1/admin/seed/runs  → bulk-creates N run records, returns { ids }
 *   3. Register a teardown hook that DELETEs the seeded records after each test.
 *
 * Until that infrastructure exists the tests are skipped; the production code change
 * (h-screen + overflow-hidden on the app shell) is implemented unconditionally.
 *
 * TODO(fixture-design): When implementing seedManyJobs / seedManyRuns, decide
 * between two approaches and apply consistently across all e2e specs:
 *   (a) Playwright fixture injection via test.extend (matches the writing-plans
 *       template's example signature: `async ({ page, seedManyJobs }) => ...`).
 *   (b) Module-level async helper functions (current stub pattern in this file).
 * Other phase 13a/b specs (e.g., detectors.spec.ts, detectors-delete tests) should
 * follow the same choice. See plan Task 3.1 + reviewer feedback.
 *
 * TODO(fixture-cleanup): When implementing the fixtures, ensure teardown runs even
 * on test failure (e.g., via test.afterEach or Playwright fixture `use(value)` with
 * a try/finally block) so seeded records are never left behind in the test database.
 */
import { test, expect, request } from "@playwright/test";

const ENABLED = process.env.LAYOUT_E2E === "1";
const API_BASE = process.env.E2E_API_BASE ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Seed helpers
// ---------------------------------------------------------------------------

/**
 * TODO(fixture): Bulk-create N job records so the /jobs DataTable overflows
 * the viewport, making it possible to assert that the sidebar logout button
 * stays visible despite long page content.
 */
async function seedManyJobs(
  _apiContext: Awaited<ReturnType<typeof request.newContext>>,
  _count: number,
): Promise<void> {
  // TODO(fixture): replace this stub with real API calls once a test-seeding
  // endpoint exists (see module-level comment above).
  throw new Error(
    "seedManyJobs is not yet implemented — needs a backend seed API. " +
      "Set LAYOUT_E2E=1 only after implementing the fixture.",
  );
}

/**
 * TODO(fixture): Bulk-create N run records so the /runs DataTable overflows
 * the viewport, making it possible to assert that the sidebar logout button
 * stays visible despite long page content.
 */
async function seedManyRuns(
  _apiContext: Awaited<ReturnType<typeof request.newContext>>,
  _count: number,
): Promise<void> {
  // TODO(fixture): replace this stub with real API calls once a test-seeding
  // endpoint exists (see module-level comment above).
  throw new Error(
    "seedManyRuns is not yet implemented — needs a backend seed API. " +
      "Set LAYOUT_E2E=1 only after implementing the fixture.",
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("App-shell layout — sidebar bottom block always visible", () => {
  test("logout button visible on /jobs even with long list", async ({ page }) => {
    test.skip(!ENABLED, "set LAYOUT_E2E=1 to enable (requires seed API)");
    test.setTimeout(30_000);

    // TODO(fixture-cleanup): once fixtures are implemented, dispose this apiContext
    // (e.g., via test.afterEach or fixture teardown) so HTTP sessions are released
    // even on test failure.
    const apiContext = await request.newContext({ baseURL: API_BASE });
    await seedManyJobs(apiContext, 80);
    await page.goto("/jobs");
    await page.waitForSelector("h1");

    const logout = page.getByRole("button", { name: /logout/i });
    await expect(logout).toBeVisible();
    expect(await logout.boundingBox()).not.toBeNull();
    const box = (await logout.boundingBox())!;
    const viewportHeight = page.viewportSize()!.height;
    expect(box.y + box.height).toBeLessThanOrEqual(viewportHeight + 1);
  });

  test("logout button visible on /runs", async ({ page }) => {
    test.skip(!ENABLED, "set LAYOUT_E2E=1 to enable (requires seed API)");
    test.setTimeout(30_000);

    // TODO(fixture-cleanup): once fixtures are implemented, dispose this apiContext
    // (e.g., via test.afterEach or fixture teardown) so HTTP sessions are released
    // even on test failure.
    const apiContext = await request.newContext({ baseURL: API_BASE });
    await seedManyRuns(apiContext, 40);
    await page.goto("/runs");
    await page.waitForSelector("h1");

    const logout = page.getByRole("button", { name: /logout/i });
    await expect(logout).toBeVisible();
  });

  test("body does not scroll; only main scrolls", async ({ page }) => {
    test.skip(!ENABLED, "set LAYOUT_E2E=1 to enable (requires seed API)");
    test.setTimeout(30_000);

    // TODO(fixture-cleanup): once fixtures are implemented, dispose this apiContext
    // (e.g., via test.afterEach or fixture teardown) so HTTP sessions are released
    // even on test failure.
    const apiContext = await request.newContext({ baseURL: API_BASE });
    await seedManyJobs(apiContext, 80);
    await page.goto("/jobs");

    const bodyScrolls = await page.evaluate(
      () => document.documentElement.scrollHeight > window.innerHeight + 1,
    );
    expect(bodyScrolls).toBe(false);

    const mainScrolls = await page.evaluate(() => {
      const main = document.querySelector("main");
      return main ? main.scrollHeight > main.clientHeight : false;
    });
    expect(mainScrolls).toBe(true);
  });

  test("logout still visible on short pages (regression)", async ({ page }) => {
    test.skip(!ENABLED, "set LAYOUT_E2E=1 to enable");
    test.setTimeout(30_000);

    await page.goto("/detectors");
    const logout = page.getByRole("button", { name: /logout/i });
    await expect(logout).toBeVisible();
  });
});
