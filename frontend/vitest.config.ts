import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  test: {
    environment: "jsdom",
    globals: true,
    // D2.6 Task 18 — MSW global lifecycle hooks live in tests/mocks/setup.ts.
    setupFiles: ["./tests/setup.ts", "./tests/mocks/setup.ts"],
    include: [
      "tests/unit/**/*.test.{ts,tsx}",
      "tests/integration/**/*.test.{ts,tsx}",
      "tests/contract/**/*.test.{ts,tsx}",
    ],
    testTimeout: 10_000,
    coverage: {
      reporter: ["text", "html", "lcov"],
      // D2.10 Task 27 — coverage extended to src/components + src/routes
      // alongside the existing src/lib + src/hooks scope.
      include: [
        "src/lib/**",
        "src/hooks/**",
        "src/components/**",
        "src/routes/**",
      ],
      thresholds: {
        lines: 70,
        functions: 70,
        statements: 70,
      },
    },
  },
});
