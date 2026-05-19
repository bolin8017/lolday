import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useResolvedTheme } from "@/hooks/useResolvedTheme";

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
    media: "(prefers-color-scheme: dark)",
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
    mql,
    listenerCount: () => listeners.length,
    fire(matches: boolean) {
      mql.matches = matches;
      listeners.forEach((cb) => cb({ matches } as MediaQueryListEvent));
    },
  };
}

describe("useResolvedTheme", () => {
  const originalClassList = document.documentElement.className;

  beforeEach(() => {
    document.documentElement.className = "";
    vi.restoreAllMocks();
  });

  afterEach(() => {
    document.documentElement.className = originalClassList;
  });

  it("returns 'dark' when <html> has the dark class", () => {
    document.documentElement.classList.add("dark");
    mockMatchMedia(false); // matchMedia branch should not be reached
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("dark");
  });

  it("returns 'light' when <html> has the light class", () => {
    document.documentElement.classList.add("light");
    mockMatchMedia(true); // would say dark if reached
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
  });

  it("falls back to OS preference when no class is set: dark", () => {
    mockMatchMedia(true);
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("dark");
  });

  it("falls back to OS preference when no class is set: light", () => {
    mockMatchMedia(false);
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
  });

  it("falls back to 'light' when matchMedia is unavailable", () => {
    // jsdom defaults provide matchMedia; explicitly remove it to hit the
    // safety branch (deployed pages with no matchMedia, e.g. SSR-shimmed
    // environments).
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: undefined,
    });
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
  });

  it("re-renders when <html> class flips light → dark via MutationObserver", async () => {
    document.documentElement.classList.add("light");
    mockMatchMedia(false);
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");

    // Flip the class — the MutationObserver subscription must trigger a
    // re-resolve. jsdom's MutationObserver fires microtask-queue style;
    // act() drains it.
    await act(async () => {
      document.documentElement.classList.remove("light");
      document.documentElement.classList.add("dark");
    });
    // The observer dispatches asynchronously; wait for a microtask flush.
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current).toBe("dark");
  });

  it("re-renders when OS preference flips via matchMedia 'change' event", () => {
    document.documentElement.classList.remove("dark", "light");
    const mm = mockMatchMedia(false);
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");

    act(() => mm.fire(true));
    expect(result.current).toBe("dark");
  });

  it("disconnects observers on unmount (no leak when consumers unmount)", () => {
    const mm = mockMatchMedia(false);
    const { unmount } = renderHook(() => useResolvedTheme());
    expect(mm.listenerCount()).toBe(1);
    unmount();
    expect(mm.listenerCount()).toBe(0);
  });
});
