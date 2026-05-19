import { describe, it, expect } from "vitest";
import { queryClient } from "@/api/queryClient";

/**
 * `queryClient` defines the cross-app TanStack Query defaults. Two policy
 * decisions are load-bearing and worth pinning:
 *
 * 1. **Retry skip for 401 / 403 / 404** — these are not transient. The
 *    `_authed` layout already routes the user back through Cloudflare
 *    Access on 401; retrying would mask the redirect with a stale spinner.
 *    403 / 404 are application state, not infra flakes.
 * 2. **Retry up to 2 attempts for everything else** — three total
 *    requests (initial + 2 retries) so transient 500 / network drops
 *    recover without blowing the user's flow.
 *
 * The MutationCache.onError toast path is harder to unit-test
 * (requires a real React + toast harness); covered indirectly through
 * the queries' `useMutation` integration tests.
 */

type Retry = (failureCount: number, error: unknown) => boolean | number;

function retry() {
  const r = queryClient.getDefaultOptions().queries?.retry;
  // `retry` is typed as `boolean | number | RetryFn` in the TanStack types.
  // The queryClient explicitly installs a function — assert that shape so
  // a future refactor that swaps in `retry: 0` will surface here.
  if (typeof r !== "function") {
    throw new Error(
      `queryClient.queries.retry expected to be a function, got: ${typeof r}`,
    );
  }
  return r as Retry;
}

describe("queryClient retry policy", () => {
  it("skips retry on 401 (CF Access JWT missing / expired)", () => {
    expect(retry()(0, { status: 401 })).toBe(false);
  });

  it("skips retry on 403 (insufficient role)", () => {
    expect(retry()(0, { status: 403 })).toBe(false);
  });

  it("skips retry on 404 (not found is application state, not flake)", () => {
    expect(retry()(0, { status: 404 })).toBe(false);
  });

  it("retries up to 2 times on transient 500", () => {
    // failureCount is the number of failures BEFORE this attempt.
    // Initial failure (count=0): retry → true
    // After 1 retry that also failed (count=1): retry → true
    // After 2 retries that all failed (count=2): retry → false
    expect(retry()(0, { status: 500 })).toBe(true);
    expect(retry()(1, { status: 500 })).toBe(true);
    expect(retry()(2, { status: 500 })).toBe(false);
  });

  it("retries on errors that lack a status field (network drop, abort)", () => {
    // A bare Error has no `status` so the 401/403/404 short-circuit doesn't
    // fire; the policy falls back to count-based retry.
    expect(retry()(0, new Error("network"))).toBe(true);
    expect(retry()(2, new Error("network"))).toBe(false);
  });

  it("retries when error is undefined / null (defensive)", () => {
    // TanStack passes through whatever the query rejected with, including
    // primitives. The `typeof error === 'object' && error !== null` guard
    // sidesteps the in-check on null without crashing.
    expect(retry()(0, undefined)).toBe(true);
    expect(retry()(0, null)).toBe(true);
    expect(retry()(2, null)).toBe(false);
  });

  it("ignores non-401/403/404 statuses (e.g. 502, 429)", () => {
    // The allowlist is exact — 502 (Cloudflare upstream) should still
    // retry, as should 429 (rate limit may clear before max attempts).
    expect(retry()(0, { status: 502 })).toBe(true);
    expect(retry()(0, { status: 429 })).toBe(true);
  });
});

describe("queryClient defaults", () => {
  it("disables refetchOnWindowFocus", () => {
    // The dashboard has many panels; refetch-on-focus would thrash the
    // backend every tab switch. Pin the choice.
    expect(queryClient.getDefaultOptions().queries?.refetchOnWindowFocus).toBe(
      false,
    );
  });

  it("sets staleTime to 30 seconds", () => {
    // 30s is the dashboard refresh granularity (panels poll on a 30s
    // interval); a shorter staleTime would re-render mid-poll, a longer
    // one would let two pollers see stale data side-by-side.
    expect(queryClient.getDefaultOptions().queries?.staleTime).toBe(30_000);
  });
});
