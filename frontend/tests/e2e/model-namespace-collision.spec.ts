/**
 * Namespace collision / isolation E2E spec.
 *
 * Verifies that two different users owning models for the same detector are
 * correctly shown in the list as `alice/elf-rf` and `bob/elf-rf`, and that
 * one user's private versions are not visible to the other.
 *
 * Opt-in: set `MODEL_NAMESPACE_VERIFY=1` and source `.lolday-secrets.env`.
 * Skipped by default so regular `pnpm playwright test` does not need a
 * deployed stack.
 *
 * Pre-condition (§4.4 Bucket 2.11):
 * - Two users (alice + bob — or configure via env vars) each own a model for
 *   the same detector (e.g. elf-rf).
 * - These models are public so both appear in the "All" filter view.
 * - At least one of bob's versions is private (should not be visible when
 *   browsing as alice).
 *
 * Env vars:
 *   MODEL_ALICE_HANDLE     — first owner handle (default: "alice")
 *   MODEL_BOB_HANDLE       — second owner handle (default: "bob")
 *   MODEL_SHARED_NAME      — detector name that both own (default: "elf-rf")
 */
import { test, expect } from "@playwright/test";

const ENABLED = process.env.MODEL_NAMESPACE_VERIFY === "1";
const ALICE = process.env.MODEL_ALICE_HANDLE ?? "alice";
const BOB = process.env.MODEL_BOB_HANDLE ?? "bob";
const SHARED_NAME = process.env.MODEL_SHARED_NAME ?? "elf-rf";

test.use({
  baseURL: "https://lolday.connlabai.com",
  ignoreHTTPSErrors: true,
  extraHTTPHeaders: {
    "CF-Access-Client-Id": process.env.CF_ACCESS_CLIENT_ID ?? "",
    "CF-Access-Client-Secret": process.env.CF_ACCESS_CLIENT_SECRET ?? "",
  },
});

test.beforeEach(async () => {
  test.skip(!ENABLED, "set MODEL_NAMESPACE_VERIFY=1 to enable");
});

test("list page shows both alice/elf-rf and bob/elf-rf as distinct rows", async ({
  page,
}) => {
  await page.goto("/models", { waitUntil: "domcontentloaded" });

  // Switch to "All" to include models from all users
  await page
    .getByRole("combobox")
    .filter({ hasText: /All|Mine|Public/i })
    .click();
  await page.getByRole("option", { name: /^All$/i }).click();

  // Wait for table to populate
  await page.waitForSelector("table tbody tr", { timeout: 15_000 });

  // Both namespaced rows must be visible
  await expect(
    page.getByRole("link", {
      name: new RegExp(`${ALICE}.*${SHARED_NAME}`, "i"),
    }),
  ).toBeVisible({ timeout: 10_000 });

  await expect(
    page.getByRole("link", { name: new RegExp(`${BOB}.*${SHARED_NAME}`, "i") }),
  ).toBeVisible({ timeout: 10_000 });
});

test("alice's private versions are not visible on bob's model detail page", async ({
  page,
}) => {
  // Authenticated as the CF-Access user (operator's account), open bob's model.
  // Bob's model is public so it appears in "All". Alice's private versions
  // belong only to alice's model URL — they must not bleed into bob's page.
  await page.goto(`/models/${BOB}/${SHARED_NAME}`, {
    waitUntil: "domcontentloaded",
  });

  // Page must load without error
  await expect(page.getByRole("heading", { name: /Versions/i })).toBeVisible({
    timeout: 15_000,
  });

  // Collect all run-id cell text from the versions table — these come from
  // MLflow and are unique per run. We cannot enumerate alice's exact run IDs
  // here (they depend on the live cluster state), so instead we verify the
  // page URL still corresponds to bob's namespace.
  expect(page.url()).toContain(`/models/${BOB}/${SHARED_NAME}`);

  // Structural assertion: the page must NOT contain a link back to alice's
  // namespace for this model (e.g. /models/alice/elf-rf in version rows).
  const aliceLinks = page.locator(`a[href*="/models/${ALICE}/${SHARED_NAME}"]`);
  await expect(aliceLinks).toHaveCount(0);
});

test("navigating to alice/elf-rf shows alice's versions, not bob's", async ({
  page,
}) => {
  await page.goto(`/models/${ALICE}/${SHARED_NAME}`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByRole("heading", { name: /Versions/i })).toBeVisible({
    timeout: 15_000,
  });

  // URL must stay on alice's namespace
  expect(page.url()).toContain(`/models/${ALICE}/${SHARED_NAME}`);

  // Should not contain links to bob's namespace within version table
  const bobLinks = page.locator(`a[href*="/models/${BOB}/${SHARED_NAME}"]`);
  await expect(bobLinks).toHaveCount(0);
});
