import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

// When testing against the deployed stack, Chromium's host-resolver-rules
// bypass the need for an /etc/hosts entry by mapping the ingress host to
// the local port-forward.
const DEPLOYED_HOST = "lolday.connlabai.com";
const deployedHostArgs = BASE_URL.includes(DEPLOYED_HOST)
  ? [`--host-resolver-rules=MAP ${DEPLOYED_HOST} 127.0.0.1`]
  : [];

const RUN_LOCAL_STACK = !BASE_URL.includes(DEPLOYED_HOST);

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  expect: { timeout: 10_000 },
  // D3.4 — fullyParallel + 4 workers + worker-aware persona via
  // helpers/auth.ts personaForWorker(). Phase 2 R4 unblocked this.
  fullyParallel: true,
  workers: 4,
  reporter: "list",
  // D3.3 — globalSetup seeds the deterministic fixture set once.
  globalSetup: "./tests/e2e/global-setup.ts",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  // D3.3 — live-stack: uvicorn (backend) + vite dev (frontend).
  webServer: RUN_LOCAL_STACK
    ? [
        {
          command:
            "cd ../backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000",
          url: "http://127.0.0.1:8000/healthz",
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
          env: {
            AUTH_DEV_MODE: "true",
            AUTH_DEV_EMAIL: "admin@dev.local",
            ENVIRONMENT: "development",
            DATABASE_URL:
              "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
            CF_ACCESS_TEAM_DOMAIN: "",
            CF_ACCESS_APP_AUD: "",
            // /openapi.json is gated by DOCS_ENABLED (default off in
            // prod). Off doesn't matter for E2E specs, but on keeps
            // parity with the regen + drift guard.
            DOCS_ENABLED: "true",
          },
        },
        {
          command: "pnpm dev",
          url: "http://127.0.0.1:5173",
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
      ]
    : undefined,
  projects: [
    {
      name: "chromium",
      // Desktop project ignores the mobile/ subdirectory; the mobile project
      // scopes itself to it via its own testDir.
      testIgnore: ["**/mobile/**"],
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: { args: deployedHostArgs },
      },
    },
    {
      name: "iphone-13-mini",
      testDir: "./tests/e2e/mobile",
      use: {
        ...devices["iPhone 13 Mini"],
        launchOptions: { args: deployedHostArgs },
      },
    },
  ],
});
