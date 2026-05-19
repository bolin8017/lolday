/**
 * Unit tests for `@/hooks/useAuth`.
 *
 * The hook is the platform's only auth-state surface — every guarded
 * page (sidebar, route layouts, role-gated UI) reads from it. It maps a
 * `useCurrentUser` query result onto four derived fields:
 *
 *   currentUser       — query.data ?? null
 *   isLoading         — query.isLoading
 *   isUnauthenticated — query.isError && error.status === 401
 *   logout            — fixed redirect to /cdn-cgi/access/logout
 *
 * The 401-detection branch is the tricky one — a `useCurrentUser` error
 * with `status: 500` must NOT flip `isUnauthenticated` true (that would
 * pop a logout banner on a transient backend outage, masking the real
 * problem). The tests below pin every branch.
 */
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

// `useCurrentUser` lives in `@/api/queries/auth` — mock it so we can
// control every field on the returned query object.
const useCurrentUserMock = vi.fn();
vi.mock("@/api/queries/auth", () => ({
  useCurrentUser: () => useCurrentUserMock(),
}));

import { useAuth } from "@/hooks/useAuth";

describe("useAuth", () => {
  let originalLocation: Location;
  beforeEach(() => {
    useCurrentUserMock.mockReset();
    // Stub `window.location` so a logout doesn't actually navigate the
    // test runner. `delete` + reassign is the JSDOM-friendly pattern.
    originalLocation = window.location;
    const navigated: { href: string } = { href: "" };
    Object.defineProperty(window, "location", {
      value: new Proxy(navigated, {
        set(target, prop, value) {
          if (prop === "href") {
            target.href = value;
          }
          return true;
        },
        get(target, prop) {
          if (prop === "href") return target.href;
          return undefined;
        },
      }),
      writable: true,
    });
  });
  afterEach(() => {
    Object.defineProperty(window, "location", {
      value: originalLocation,
      writable: true,
    });
  });

  it("returns currentUser=null while loading (no data yet)", () => {
    useCurrentUserMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
    });
    const { result } = renderHook(() => useAuth());
    expect(result.current.currentUser).toBeNull();
    expect(result.current.isLoading).toBe(true);
    expect(result.current.isUnauthenticated).toBe(false);
  });

  it("returns the user object when the query resolves", () => {
    const user = { email: "alice@x.com", handle: "alice", role: "developer" };
    useCurrentUserMock.mockReturnValue({
      data: user,
      isLoading: false,
      isError: false,
      error: null,
    });
    const { result } = renderHook(() => useAuth());
    expect(result.current.currentUser).toEqual(user);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.isUnauthenticated).toBe(false);
  });

  it("flips isUnauthenticated=true ONLY on 401 errors", () => {
    useCurrentUserMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: { status: 401 },
    });
    const { result } = renderHook(() => useAuth());
    expect(result.current.isUnauthenticated).toBe(true);
    expect(result.current.currentUser).toBeNull();
  });

  it("does NOT flip isUnauthenticated on non-401 errors (e.g. 500)", () => {
    // Critical: a transient backend outage (500) must NOT trigger a
    // logout banner. Pinning this prevents a refactor that drops the
    // `=== 401` narrow check.
    useCurrentUserMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: { status: 500 },
    });
    const { result } = renderHook(() => useAuth());
    expect(result.current.isUnauthenticated).toBe(false);
  });

  it("does NOT flip isUnauthenticated on a thrown error without a status field", () => {
    // Network errors (CORS, ECONNRESET) reach tanstack-query with no
    // `status` field. Same defence as above.
    useCurrentUserMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("network down"),
    });
    const { result } = renderHook(() => useAuth());
    expect(result.current.isUnauthenticated).toBe(false);
  });

  it("logout() redirects to the Cloudflare Access logout endpoint", () => {
    useCurrentUserMock.mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      error: null,
    });
    const { result } = renderHook(() => useAuth());
    result.current.logout();
    // The hook redirects via assignment to `window.location.href`. The
    // proxy installed in beforeEach captures the assignment.
    expect(window.location.href).toBe("/cdn-cgi/access/logout");
  });
});
