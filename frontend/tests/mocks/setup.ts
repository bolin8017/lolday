import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "./server";

/**
 * D2.6 Task 18 — vitest global MSW setup.
 *
 * onUnhandledRequest: "error" enforces anti-flaky rule #1 (no un-mocked
 * network in tests). Any new endpoint a component starts calling must
 * either be listed in tests/mocks/handlers.ts or stubbed in-test via
 * server.use(...).
 *
 * resetHandlers() between tests prevents bleed-over from in-test
 * server.use() calls into the next test.
 */
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
