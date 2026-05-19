import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { useIsMobile } from "@/hooks/useIsMobile";

/**
 * `useIsMobile` is the breakpoint sensor used by the Sidebar block + a
 * handful of responsive components. The threshold (`< 768px`) intentionally
 * matches shadcn/ui's Sidebar's internal threshold — drift would silently
 * desync our `useIsMobile` from the Sidebar's own check and produce a
 * "half-mobile" layout (collapsed nav + desktop content widths).
 *
 * The global `window.matchMedia` stub in `tests/setup.ts` returns
 * `matches: false` by default; per-test overrides via Object.defineProperty
 * are the documented escape hatch.
 */

function installMatchMedia(
  matches: boolean,
  listeners: {
    add: ReturnType<typeof vi.fn>;
    remove: ReturnType<typeof vi.fn>;
  },
) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches,
      media: "",
      addEventListener: listeners.add,
      removeEventListener: listeners.remove,
      dispatchEvent: () => true,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
    }),
  });
}

describe("useIsMobile", () => {
  it("returns false when the viewport is above the mobile breakpoint", () => {
    // Default setup.ts stub: matches=false.
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("returns true when matchMedia reports the mobile breakpoint", () => {
    installMatchMedia(true, { add: vi.fn(), remove: vi.fn() });
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("queries the canonical `(max-width: 767px)` media query", () => {
    // The 767px threshold matches shadcn/ui's Sidebar — drift causes a
    // half-mobile layout. Pin the query string so a future "let's bump
    // to 768/640/600" tweak surfaces in PR review.
    const spy = vi.fn().mockReturnValue({
      matches: false,
      media: "",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: () => true,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
    });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: spy,
    });
    renderHook(() => useIsMobile());
    // Called twice — once at useState lazy init, once inside useEffect.
    expect(spy).toHaveBeenCalledWith("(max-width: 767px)");
  });

  it("updates when matchMedia fires a change event", () => {
    let changeHandler: ((e: { matches: boolean }) => void) | null = null;
    const add = vi.fn((_evt: string, cb: (e: { matches: boolean }) => void) => {
      changeHandler = cb;
    });
    installMatchMedia(false, { add, remove: vi.fn() });
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
    expect(changeHandler).not.toBeNull();

    act(() => {
      // Simulate viewport drop below 768px — what happens when the user
      // drags the browser narrower.
      changeHandler!({ matches: true });
    });
    expect(result.current).toBe(true);
  });

  it("unsubscribes from matchMedia on unmount", () => {
    const add = vi.fn();
    const remove = vi.fn();
    installMatchMedia(false, { add, remove });
    const { unmount } = renderHook(() => useIsMobile());
    expect(add).toHaveBeenCalledTimes(1);
    unmount();
    // The removeEventListener call MUST pass the same handler that was
    // registered. Without that, a long-lived MediaQueryList keeps the
    // unmounted hook alive (a leak on every route change).
    expect(remove).toHaveBeenCalledTimes(1);
    expect(remove).toHaveBeenCalledWith("change", add.mock.calls[0][1]);
  });
});
