import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

// When testing against the deployed stack, Chromium's host-resolver-rules
// bypass the need for an /etc/hosts entry by mapping the ingress host to
// the local port-forward.
const DEPLOYED_HOST = "lolday.connlabai.com";
const deployedHostArgs = BASE_URL.includes(DEPLOYED_HOST)
  ? [`--host-resolver-rules=MAP ${DEPLOYED_HOST} 127.0.0.1`]
  : [];

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  expect: { timeout: 10_000 },
  fullyParallel: false, // tests share backend state; keep sequential
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
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
    // Pixel 5 (393×851) was removed: shares the same 393 px width as
    // iPhone 13 Mini, both use Playwright's Chromium internally, and the
    // height delta (812 vs 851) does not exercise meaningfully different
    // CSS / layout. Add it back when a real Android-vs-iOS-Safari
    // behavioural divergence needs coverage (Playwright Webkit project
    // would be more useful than another Chromium device descriptor).
  ],
});
