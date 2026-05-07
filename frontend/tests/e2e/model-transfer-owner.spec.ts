/**
 * Model owner transfer E2E spec.
 *
 * Opt-in: set `MODEL_NAMESPACE_VERIFY=1` and source `.lolday-secrets.env`.
 * Skipped by default so regular `pnpm playwright test` does not need a
 * deployed stack.
 *
 * Pre-condition:
 * - At least one model owned by the authenticated user.
 * - A second user handle exists in the system (set MODEL_TRANSFER_TARGET
 *   to a valid handle; defaults to "alice" per §4.4 test fixtures).
 * - "alice" does NOT already own a model of the same detector as the model
 *   being transferred (so the happy-path test can proceed).
 *
 * Note: the transfer-and-redirect test mutates state. Operator should run
 * the post-deploy wipe or manually restore ownership afterwards.
 * For CI safety, this test is deliberately skipped when the opt-in env var
 * is absent.
 */
import { test, expect } from "@playwright/test";

const ENABLED = process.env.MODEL_NAMESPACE_VERIFY === "1";
const TRANSFER_TARGET = process.env.MODEL_TRANSFER_TARGET ?? "alice";

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
  // Extract owner + name from URL: .../models/{owner}/{name}
  const parts = page
    .url()
    .replace(/.*\/models\//, "")
    .split("/");
  return { owner: parts[0], name: parts[1] };
}

test("owner transfers model to another user — URL updates to new owner namespace", async ({
  page,
}) => {
  const { name } = await openFirstOwnedModel(page);

  // Open model-level kebab
  await page.getByRole("button", { name: /more/i }).first().click();
  await page.getByRole("menuitem", { name: /Transfer ownership/i }).click();

  await expect(
    page.getByRole("dialog").getByRole("heading", {
      name: /Transfer ownership/i,
    }),
  ).toBeVisible({ timeout: 5_000 });

  // Fill new owner handle
  await page.getByLabel(/New owner handle/i).fill(TRANSFER_TARGET);

  // Submit
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /^Transfer$/i })
    .click();

  // Should navigate to new owner's namespace
  await page.waitForURL(new RegExp(`/models/${TRANSFER_TARGET}/${name}`), {
    timeout: 15_000,
  });
  expect(page.url()).toContain(`/models/${TRANSFER_TARGET}/${name}`);

  // Toast
  await expect(page.getByText(/Ownership transferred/i)).toBeVisible({
    timeout: 10_000,
  });
});

test("transfer to nonexistent handle shows error", async ({ page }) => {
  await openFirstOwnedModel(page);

  await page.getByRole("button", { name: /more/i }).first().click();
  await page.getByRole("menuitem", { name: /Transfer ownership/i }).click();

  await expect(
    page.getByRole("dialog").getByRole("heading", {
      name: /Transfer ownership/i,
    }),
  ).toBeVisible({ timeout: 5_000 });

  // Use a handle that almost certainly does not exist
  await page.getByLabel(/New owner handle/i).fill("__nonexistent_handle_xyz__");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /^Transfer$/i })
    .click();

  // Expect an error — either a toast or an error message in the dialog.
  // The backend returns 422 for unknown handle.
  await expect(
    page
      .getByRole("alert")
      .or(page.getByText(/Error|error|not found|422/i))
      .first(),
  ).toBeVisible({ timeout: 10_000 });
});

test("transfer dialog submit button is disabled when handle field is empty", async ({
  page,
}) => {
  await openFirstOwnedModel(page);

  await page.getByRole("button", { name: /more/i }).first().click();
  await page.getByRole("menuitem", { name: /Transfer ownership/i }).click();

  await expect(
    page.getByRole("dialog").getByRole("heading", {
      name: /Transfer ownership/i,
    }),
  ).toBeVisible({ timeout: 5_000 });

  // The handle input is empty on open
  const submitBtn = page
    .getByRole("dialog")
    .getByRole("button", { name: /^Transfer$/i });

  await expect(submitBtn).toBeDisabled();
});
