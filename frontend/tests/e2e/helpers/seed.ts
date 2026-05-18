/**
 * Shared seed-fixture restoration helper.
 *
 * `frontend/playwright.config.ts` runs `fullyParallel: true, workers: 4`.
 * `globalSetup` calls `POST /api/v1/dev/seed-fixtures` once before any
 * worker starts. Any spec that mutates the shared seed (notably
 * `models/transfer-and-delete.spec.ts`, which DELETEs the
 * `RegisteredModel`) leaves parallel specs racing the destructive
 * window. The pragmatic mitigation is for vulnerable read-only specs to
 * defensively re-seed in `test.beforeEach`; the endpoint is idempotent
 * (UUID5-keyed `session.get` + early return) so a re-call costs ~200 ms
 * when the rows already exist.
 *
 * See `[[project_destructive_e2e_specs_need_afterEach_reseed]]` for the
 * full incident note + the long-term per-worker-isolation path.
 */
import type { Browser } from "@playwright/test";

export async function reseedAsAdmin(browser: Browser): Promise<void> {
  const ctx = await browser.newContext({
    extraHTTPHeaders: { "X-Dev-Persona": "admin" },
  });
  await ctx.request.post("/api/v1/dev/seed-fixtures");
  await ctx.close();
}
