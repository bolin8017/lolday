/**
 * Model delete flow E2E spec.
 *
 * Opt-in: set `MODEL_NAMESPACE_VERIFY=1` and source `.lolday-secrets.env`.
 * Skipped by default so regular `pnpm playwright test` does not need a
 * deployed stack.
 *
 * Pre-condition: at least one model owned by the authenticated user.
 * The "delete succeeds" test is DESTRUCTIVE — it permanently removes the
 * model and all versions. Operator should only enable during a dedicated
 * teardown / re-seed pass (see Phase C T30 §4.4 bucket 2.12).
 *
 * Set MODEL_DELETE_DESTRUCTIVE=1 in addition to MODEL_NAMESPACE_VERIFY=1
 * to run the destructive delete test. The type-to-confirm gating test is
 * non-destructive and runs whenever MODEL_NAMESPACE_VERIFY=1.
 */
import { test, expect } from "@playwright/test";

const ENABLED = process.env.MODEL_NAMESPACE_VERIFY === "1";
const DESTRUCTIVE = process.env.MODEL_DELETE_DESTRUCTIVE === "1";

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

/** Navigate to the detail page of the first owned model. */
async function openFirstOwnedModel(
  page: import("@playwright/test").Page,
): Promise<{ owner: string; name: string }> {
  await page.goto("/models", { waitUntil: "domcontentloaded" });
  await page
    .getByRole("combobox")
    .filter({ hasText: /All|Mine|Public/i })
    .click();
  await page.getByRole("option", { name: /Mine/i }).click();
  await page
    .locator("table tbody tr")
    .first()
    .getByRole("link")
    .first()
    .click();
  await page.waitForURL(/\/models\/[^/]+\/[^/]+/);
  const parts = page
    .url()
    .replace(/.*\/models\//, "")
    .split("/");
  return { owner: parts[0], name: parts[1] };
}

test("delete button stays disabled until exact owner/name is typed", async ({
  page,
}) => {
  const { owner, name } = await openFirstOwnedModel(page);
  const fullName = `${owner}/${name}`;

  // Open model-level kebab → Delete model
  await page.getByRole("button", { name: /more/i }).first().click();
  await page.getByRole("menuitem", { name: /Delete model/i }).click();

  await expect(
    page.getByRole("dialog").getByRole("heading", {
      name: /Delete model/i,
    }),
  ).toBeVisible({ timeout: 5_000 });

  const deleteBtn = page
    .getByRole("dialog")
    .getByRole("button", { name: /^Delete$/i });

  // Initially disabled
  await expect(deleteBtn).toBeDisabled();

  // Type a wrong value — still disabled
  await page.getByRole("dialog").locator("input").fill("wrong-value");
  await expect(deleteBtn).toBeDisabled();

  // Type correct fullName — should become enabled
  await page.getByRole("dialog").locator("input").fill(fullName);
  await expect(deleteBtn).toBeEnabled();

  // Close without deleting
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /Cancel/i })
    .click();
});

test("delete model removes it from list (destructive — requires MODEL_DELETE_DESTRUCTIVE=1)", async ({
  page,
}) => {
  test.skip(
    !DESTRUCTIVE,
    "set MODEL_DELETE_DESTRUCTIVE=1 to run destructive delete",
  );

  const { owner, name } = await openFirstOwnedModel(page);
  const fullName = `${owner}/${name}`;

  // Open delete dialog
  await page.getByRole("button", { name: /more/i }).first().click();
  await page.getByRole("menuitem", { name: /Delete model/i }).click();

  await expect(
    page.getByRole("dialog").getByRole("heading", {
      name: /Delete model/i,
    }),
  ).toBeVisible({ timeout: 5_000 });

  // Type confirm string and delete
  await page.getByRole("dialog").locator("input").fill(fullName);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /^Delete$/i })
    .click();

  // Should redirect to /models list
  await page.waitForURL(/\/models$/, { timeout: 15_000 });

  // Toast
  await expect(page.getByText(/Model deleted/i)).toBeVisible({
    timeout: 10_000,
  });

  // Filter to Mine — the deleted model should no longer appear in the table
  await page
    .getByRole("combobox")
    .filter({ hasText: /All|Mine|Public/i })
    .click();
  await page.getByRole("option", { name: /Mine/i }).click();

  // Wait for the table (or empty state) to stabilise
  await page.waitForTimeout(1_000);
  const rows = await page.locator("table tbody tr").count();
  if (rows > 0) {
    // Verify none of the rows contain the deleted model's full name
    const cellTexts = await page.locator("table tbody tr").allTextContents();
    for (const text of cellTexts) {
      expect(text).not.toContain(name);
    }
  }
  // rows === 0 is also a valid pass (only model was deleted)
});
