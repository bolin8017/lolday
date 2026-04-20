import type { Page } from "@playwright/test";

export interface SeedCreds {
  email: string;
  password: string;
}

/**
 * Credentials pulled from env — set E2E_ADMIN_EMAIL and E2E_ADMIN_PASSWORD
 * (usually same as ~/.lolday-secrets.env ADMIN_EMAIL/ADMIN_PASSWORD).
 */
export function seedCreds(): SeedCreds {
  const email = process.env.E2E_ADMIN_EMAIL;
  const password = process.env.E2E_ADMIN_PASSWORD;
  if (!email || !password) {
    throw new Error("Set E2E_ADMIN_EMAIL and E2E_ADMIN_PASSWORD before running E2E.");
  }
  return { email, password };
}

export async function login(page: Page, creds: SeedCreds = seedCreds()) {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill(creds.email);
  await page.getByLabel(/password/i).fill(creds.password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/(detectors|)$/);
}
