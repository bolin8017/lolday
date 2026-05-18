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
          url: "http://127.0.0.1:8000/api/v1/health",
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
            // Install in-process K8s + MLflow stubs at lifespan start
            // so this live-stack doesn't reach the operator's cluster
            // and doesn't crash on CI runners with no kubeconfig.
            // Spec: docs/superpowers/specs/2026-05-17-frontend-slow-stub-layer-design.md.
            SPEC_LANE_STUBS: "true",
            // The fixture seeds one QUEUED_BACKEND job per persona; with
            // `JOB_PER_USER_CONCURRENCY=2` (default), admin's first parallel
            // submit (job-train / full-lifecycle) hits the cap, and a third
            // submit (mobile/job-submit) 429s with `concurrency_limit`
            // before navigating. Raise the cap for the live-stack so
            // parallel specs can co-exist; production keeps the 2 default.
            JOB_PER_USER_CONCURRENCY: "50",
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
        // `devices["iPhone 13 Mini"]` defaults to `defaultBrowserType:
        // "webkit"` (Mobile Safari emulation). WebKit requires 191
        // GTK / GStreamer / fontconfig system packages that need root
        // to apt-install, while Chromium ships as a self-contained
        // headless shell that Playwright installs into
        // `~/.cache/ms-playwright/` without sudo. Override to chromium
        // here so the project runs in any user-level dev env. CI runners
        // have the WebKit deps pre-installed and `frontend-slow.yml`
        // can pin the project back to webkit if Safari-specific bug
        // coverage is ever needed; today none of the 10 mobile specs
        // in issue #245 exercise WebKit-specific rendering — failures
        // are all locator-timeout, viewport-math, or POM-flow bugs.
        defaultBrowserType: "chromium",
        launchOptions: { args: deployedHostArgs },
      },
    },
  ],
});
