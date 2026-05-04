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
 * TODO(non-admin-persona): backend's AUTH_DEV_MODE only honours a single
 * `AUTH_DEV_EMAIL`, so every spec runs as the same admin user. To exercise
 * the negative side of role-gated UI (admin-only nav links, role-mutation
 * buttons hidden for `developer` / `user`), the backend needs to honour a
 * per-request override (e.g. an `X-Dev-User-Email` header) that AUTH_DEV_MODE
 * resolves into a synthetic user with the matching role. Tracked in
 * `docs/architecture.md` §9 #14. Until then, sidebar-drawer.spec's
 * admin-link assertion is positive-case only.
 */
