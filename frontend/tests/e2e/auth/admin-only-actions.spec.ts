import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";

/**
 * D3.2 — admin-only mutating actions.
 *
 * The admin-only path the operator runbook exercises (admin-priority.md):
 *   PATCH /api/v1/jobs/{id}  body={priority: 100}
 * The backend gates with require_role(Role.ADMIN). Non-admin personas
 * must receive 403.
 *
 * Uses the dev-seed fixture's known queued_job_id so the assertion stays
 * deterministic across runs.
 */
test.describe("admin-only PATCH /jobs/{id} priority", () => {
  test("admin persona can PATCH job priority (200)", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/");
    const seedResp = await page.request.post("/api/v1/dev/seed-fixtures");
    const seed = await seedResp.json();
    const resp = await page.request.patch(
      `/api/v1/jobs/${seed.queued_job_id}`,
      { data: { priority: 100 } },
    );
    expect(resp.status()).toBe(200);
  });

  test("developer persona is rejected (403)", async ({ page }) => {
    await loginAs(page, "developer");
    await page.goto("/");
    const seedResp = await page.request.post("/api/v1/dev/seed-fixtures");
    const seed = await seedResp.json();
    const resp = await page.request.patch(
      `/api/v1/jobs/${seed.queued_job_id}`,
      { data: { priority: 100 } },
    );
    expect(resp.status()).toBe(403);
  });

  test("user persona is rejected (403)", async ({ page }) => {
    await loginAs(page, "user");
    await page.goto("/");
    const seedResp = await page.request.post("/api/v1/dev/seed-fixtures");
    const seed = await seedResp.json();
    const resp = await page.request.patch(
      `/api/v1/jobs/${seed.queued_job_id}`,
      { data: { priority: 100 } },
    );
    expect(resp.status()).toBe(403);
  });
});
