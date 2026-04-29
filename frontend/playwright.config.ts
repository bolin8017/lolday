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
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: { args: deployedHostArgs },
      },
    },
  ],
});
