import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("register upxelfdet + trigger build + wait for success", async ({
  page,
}) => {
  test.setTimeout(10 * 60_000); // builds can take a few minutes
  await login(page);

  // Ensure PAT is set (precondition) — navigate to profile and wait for data
  await page.goto("/profile");
  await page.waitForLoadState("networkidle");

  // Wait until the credential form settles: either "GitHub PAT is set" or the PAT input
  const patSetText = page.getByText(/GitHub PAT is set/i);
  const patLabel = page.getByLabel(/GitHub PAT/i);
  await Promise.race([
    patSetText.waitFor({ state: "visible", timeout: 15_000 }).catch(() => null),
    patLabel.waitFor({ state: "visible", timeout: 15_000 }).catch(() => null),
  ]);

  const patAlreadySet = await patSetText.isVisible().catch(() => false);
  if (!patAlreadySet) {
    const token = process.env.E2E_GITHUB_PAT;
    test.skip(!token, "Set E2E_GITHUB_PAT to run this spec end-to-end.");
    await patLabel.fill(token!);
    await page.getByRole("button", { name: /^Save$/i }).click();
    await expect(patSetText).toBeVisible();
  }

  // Go to detectors list; register upxelfdet if not already present
  await page.goto("/detectors");
  await page.waitForLoadState("networkidle");
  const existing = page.getByRole("cell", { name: /upx/i }).first();
  if (!(await existing.isVisible().catch(() => false))) {
    await page.getByRole("link", { name: /register/i }).click();
    await page.getByLabel(/^Name/i).fill("upxelfdet");
    await page.getByLabel(/Display name/i).fill("UPX ELF Detector");
    await page
      .getByLabel(/Git URL/i)
      .fill("https://github.com/bolin8017/upxelfdet");
    await page.getByRole("button", { name: /register detector/i }).click();
    await page.waitForURL(/\/detectors\/[0-9a-f-]+/);
  } else {
    await existing.click();
    await page.waitForURL(/\/detectors\/[0-9a-f-]+/);
  }

  // Trigger build of v0.5.0 from Builds tab
  await page.getByRole("tab", { name: /builds/i }).click();
  await page.getByRole("button", { name: /trigger build/i }).click();
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: /v0\.5\.0/ }).click();
  await page.getByRole("button", { name: /^Build$/ }).click();
  // Dialog should close automatically after the mutation resolves; ensure it's
  // gone before asserting on the underlying table (Radix sets aria-hidden on
  // background content while the Dialog is open).
  await page
    .getByRole("dialog")
    .waitFor({ state: "hidden", timeout: 15_000 })
    .catch(async () => {
      await page.keyboard.press("Escape");
      await page
        .getByRole("dialog")
        .waitFor({ state: "hidden", timeout: 5_000 });
    });

  // Wait for the newly-triggered build row to reach "Success"
  await expect(
    page.getByRole("cell", { name: /v0\.5\.0/ }).first(),
  ).toBeVisible();
  await expect(page.getByText(/Succeeded/i).first()).toBeVisible({
    timeout: 8 * 60_000,
  });
});
