/**
 * D3.3 — playwright globalSetup.
 *
 * Runs once before any worker spawns. Hits the dev-mode seed endpoint
 * (closes architecture.md §10 #12) so every spec sees the deterministic
 * fixture set. The seed endpoint is idempotent, so re-running playwright
 * locally does not pollute or duplicate.
 */
import { request } from "@playwright/test";

export default async function globalSetup() {
  const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:5173";
  const ctx = await request.newContext({
    baseURL,
    extraHTTPHeaders: { "X-Dev-Persona": "admin" },
  });
  const resp = await ctx.post("/api/v1/dev/seed-fixtures");
  if (!resp.ok()) {
    throw new Error(
      `globalSetup: seed-fixtures failed ${resp.status()}: ${await resp.text()}`,
    );
  }
  await ctx.dispose();
}
