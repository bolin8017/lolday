import { renderHook, act } from "@testing-library/react";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { useIsMobile } from "@/hooks/useIsMobile";

type Listener = (e: MediaQueryListEvent) => void;

function mockMatchMedia(initialMatches: boolean) {
  const listeners: Listener[] = [];
  const mql = {
    matches: initialMatches,
    media: "(max-width: 767px)",
    addEventListener: (_: string, cb: Listener) => listeners.push(cb),
    removeEventListener: (_: string, cb: Listener) => {
      const i = listeners.indexOf(cb);
      if (i >= 0) listeners.splice(i, 1);
    },
    dispatchEvent: () => true,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
  } as unknown as MediaQueryList;
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue(mql),
  });
  return {
    fire(matches: boolean) {
      mql.matches = matches;
      listeners.forEach((cb) => cb({ matches } as MediaQueryListEvent));
    },
  };
}

describe("useIsMobile", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("returns true when viewport matches mobile media query", () => {
    mockMatchMedia(true);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("returns false when viewport does not match", () => {
    mockMatchMedia(false);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("re-renders when viewport crosses the breakpoint", () => {
    const ctrl = mockMatchMedia(false);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
    act(() => ctrl.fire(true));
    expect(result.current).toBe(true);
  });
});
