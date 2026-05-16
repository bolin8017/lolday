import type { Page } from "@playwright/test";

export interface SeedCreds {
  email: string;
  password: string;
}

/**
 * Phase 10.2: the old password-based `login()` flow is gone. Primary auth
 * is now Cloudflare Access + GitHub OAuth at the edge. For local/CI E2E
 * runs, set the backend's `AUTH_DEV_MODE=true` + `AUTH_DEV_EMAIL=<admin>`
 * so `cf_access_user` returns a synthetic admin user without needing a
 * real Cloudflare JWT. The remaining specs then navigate straight to `/`
 * and the backend treats every request as the dev user.
 *
 * The stub `login()` below is kept so the existing specs compile while
 * we migrate the suite over.
 */
export function seedCreds(): SeedCreds {
  const email = process.env.E2E_ADMIN_EMAIL ?? "admin@lolday.dev";
  const password = process.env.E2E_ADMIN_PASSWORD ?? "";
  return { email, password };
}

export async function login(page: Page, _creds: SeedCreds = seedCreds()) {
  // With AUTH_DEV_MODE enabled server-side the app authenticates on the
  // first request — no login form to fill. Just land on the root.
  await page.goto("/");
  await page.waitForURL(/\/(detectors|)$/);
}

/**
 * D2.2 / R4 — multi-persona dev auth.
 *
 * Backend AUTH_DEV_MODE honours an `X-Dev-Persona` request header that
 * resolves to one of `AUTH_DEV_PERSONAS` (admin / developer / user) with
 * the corresponding email + role. Closes architecture.md §10 #13 (the
 * single-persona limitation) and unblocks Phase 3 multi-persona Playwright
 * parallel.
 *
 * Usage:
 *   await loginAs(page, "admin");
 *   await page.goto("/admin");
 */
export type DevPersona = "admin" | "developer" | "user";

export async function loginAs(page: Page, role: DevPersona): Promise<void> {
  await page.context().setExtraHTTPHeaders({ "X-Dev-Persona": role });
  // Reload (if already on a page) so the next render reads /users/me with
  // the new persona; on a fresh page, the next navigation picks it up.
  const url = page.url();
  if (url && url !== "about:blank") {
    await page.reload();
  }
}

/**
 * D3.4 — worker-aware persona for `fullyParallel: true` runs.
 *
 * Mod-3 cycle through admin / developer / user. Tests that need a
 * specific persona still call `loginAs(page, "admin")` directly; this
 * helper picks the default for the worker so each test gets a
 * deterministic identity without leaking state across workers.
 */
const PERSONAS_ROTATION: readonly DevPersona[] = ["admin", "developer", "user"];

export function personaForWorker(workerIndex: number): DevPersona {
  if (workerIndex < 0 || !Number.isInteger(workerIndex)) {
    throw new Error(
      `personaForWorker expects a non-negative integer worker index; got ${workerIndex}`,
    );
  }
  return PERSONAS_ROTATION[workerIndex % PERSONAS_ROTATION.length];
}
