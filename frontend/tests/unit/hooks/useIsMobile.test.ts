import { renderHook, act } from "@testing-library/react";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { useIsMobile } from "@/hooks/useIsMobile";

type Listener = (e: MediaQueryListEvent) => void;

type MockMql = {
  matches: boolean;
  media: string;
  addEventListener: (type: string, cb: Listener) => void;
  removeEventListener: (type: string, cb: Listener) => void;
  dispatchEvent: () => boolean;
  onchange: null;
  addListener: () => void;
  removeListener: () => void;
};

function mockMatchMedia(initialMatches: boolean) {
  const listeners: Listener[] = [];
  const mql: MockMql = {
    matches: initialMatches,
    media: "(max-width: 767px)",
    addEventListener: (_, cb) => listeners.push(cb),
    removeEventListener: (_, cb) => {
      const i = listeners.indexOf(cb);
      if (i >= 0) listeners.splice(i, 1);
    },
    dispatchEvent: () => true,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
  };
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
