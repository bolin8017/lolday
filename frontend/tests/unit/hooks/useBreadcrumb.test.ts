/**
 * Unit tests for `@/hooks/useBreadcrumb`.
 *
 * The hook reads `useMatches()` from react-router 7 and emits one
 * `{pathname, label}` entry per match that exposes a
 * `handle.breadcrumb` field. The field is either a literal string or a
 * `(data: unknown) => string` callback evaluated against the match's
 * loader data.
 *
 * Coverage targets:
 *   - matches without a `handle` are skipped silently
 *   - `handle` without a `breadcrumb` key is skipped silently
 *   - literal-string `breadcrumb` flows through verbatim
 *   - function `breadcrumb` is invoked against `match.data`
 *   - ordering follows `useMatches()` ordering (root → leaf)
 *
 * The mock for `useMatches` exposes the test-controlled match list per
 * case; the hook itself does not import anything else from the router
 * besides this function.
 */
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const useMatchesMock = vi.fn();
vi.mock("react-router", () => ({
  useMatches: () => useMatchesMock(),
}));

import { useBreadcrumb } from "@/hooks/useBreadcrumb";

type Match = {
  pathname: string;
  data: unknown;
  handle?: unknown;
};

function setMatches(matches: Match[]) {
  useMatchesMock.mockReturnValue(matches);
}

describe("useBreadcrumb", () => {
  it("returns empty array when no match has a handle", () => {
    setMatches([
      { pathname: "/", data: null },
      { pathname: "/jobs", data: null },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([]);
  });

  it("skips matches whose handle lacks a breadcrumb key", () => {
    setMatches([
      { pathname: "/", data: null, handle: { somethingElse: "x" } },
      { pathname: "/jobs", data: null, handle: { breadcrumb: "Jobs" } },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([{ pathname: "/jobs", label: "Jobs" }]);
  });

  it("emits literal-string breadcrumbs verbatim", () => {
    setMatches([
      { pathname: "/", data: null, handle: { breadcrumb: "Home" } },
      { pathname: "/jobs", data: null, handle: { breadcrumb: "Jobs" } },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([
      { pathname: "/", label: "Home" },
      { pathname: "/jobs", label: "Jobs" },
    ]);
  });

  it("invokes function breadcrumbs against match.data", () => {
    setMatches([
      {
        pathname: "/jobs/abc",
        data: { id: "abc", type: "train" },
        handle: {
          breadcrumb: (d: unknown) =>
            `Job ${(d as { type: string }).type} #${(d as { id: string }).id}`,
        },
      },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([
      { pathname: "/jobs/abc", label: "Job train #abc" },
    ]);
  });

  it("preserves match order (root → leaf as useMatches returns them)", () => {
    setMatches([
      { pathname: "/", data: null, handle: { breadcrumb: "Home" } },
      { pathname: "/jobs", data: null, handle: { breadcrumb: "Jobs" } },
      {
        pathname: "/jobs/abc",
        data: { id: "abc" },
        handle: { breadcrumb: (d: unknown) => (d as { id: string }).id },
      },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current.map((c) => c.pathname)).toEqual([
      "/",
      "/jobs",
      "/jobs/abc",
    ]);
    expect(result.current.map((c) => c.label)).toEqual(["Home", "Jobs", "abc"]);
  });

  it("treats handle=null as 'no handle' (filter survives null handle)", () => {
    // react-router's typing allows handle to be unknown; runtime check
    // narrows. Pin the null path so a future refactor doesn't drop the
    // explicit `m.handle !== null` guard.
    setMatches([
      { pathname: "/null-handle", data: null, handle: null },
      {
        pathname: "/with-handle",
        data: null,
        handle: { breadcrumb: "kept" },
      },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([
      { pathname: "/with-handle", label: "kept" },
    ]);
  });
});
