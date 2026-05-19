import { renderHook, act, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { useResolvedTheme } from "@/hooks/useResolvedTheme";

/**
 * `useResolvedTheme` returns whatever theme the ThemeProvider has applied to
 * `<html>`. It reads three signals, in priority order:
 *
 * 1. `<html>` carries `class="dark"` → `"dark"`.
 * 2. `<html>` carries `class="light"` → `"light"`.
 * 3. Otherwise: `prefers-color-scheme: dark` via matchMedia.
 *
 * It also subscribes to:
 * - MutationObserver on `<html>`'s class attribute (theme toggle re-renders)
 * - matchMedia `change` (OS-pref change re-renders when no explicit class)
 *
 * The chart panels + log viewer key off this hook to pick their palette.
 * A regression that silently defaulted to "light" would invert log
 * highlighting against a dark background.
 */

beforeEach(() => {
  // Reset the <html> class between tests so leftovers don't leak.
  document.documentElement.classList.remove("dark", "light");
});

afterEach(() => {
  document.documentElement.classList.remove("dark", "light");
});

describe("useResolvedTheme — read() priority", () => {
  it("returns 'dark' when <html> carries the `dark` class", () => {
    document.documentElement.classList.add("dark");
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("dark");
  });

  it("returns 'light' when <html> carries the `light` class", () => {
    document.documentElement.classList.add("light");
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
  });

  it("falls back to prefers-color-scheme when no class is set", () => {
    // setup.ts stubs matchMedia to matches:false, so prefers-color-scheme:dark
    // is false → "light". Override to flip the default once.
    const original = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: true,
        media: "(prefers-color-scheme: dark)",
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: () => true,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    });
    try {
      const { result } = renderHook(() => useResolvedTheme());
      expect(result.current).toBe("dark");
    } finally {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: original,
      });
    }
  });

  it("defaults to 'light' when no class is set and prefers-color-scheme is light", () => {
    // setup.ts stub already returns matches:false; no override needed.
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
  });

  it("prefers the explicit `dark` class over the OS preference", () => {
    // Override matchMedia so prefers-color-scheme: dark would say "dark"
    // anyway — the class-check must still win the resolution order, which
    // matters when the user has toggled the in-app theme away from OS pref.
    const original = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        media: "",
        dispatchEvent: () => true,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    });
    try {
      document.documentElement.classList.add("dark");
      const { result } = renderHook(() => useResolvedTheme());
      expect(result.current).toBe("dark");
    } finally {
      Object.defineProperty(window, "matchMedia", {
        configurable: true,
        value: original,
      });
    }
  });
});

describe("useResolvedTheme — reactivity", () => {
  it("re-renders when the <html> class toggles to dark", async () => {
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
    act(() => {
      document.documentElement.classList.add("dark");
    });
    // MutationObserver fires asynchronously in jsdom; waitFor polls until
    // the callback has executed and the state has propagated.
    await waitFor(() => expect(result.current).toBe("dark"));
  });

  it("re-renders when the <html> class toggles from dark to light", async () => {
    document.documentElement.classList.add("dark");
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("dark");
    act(() => {
      document.documentElement.classList.remove("dark");
      document.documentElement.classList.add("light");
    });
    await waitFor(() => expect(result.current).toBe("light"));
  });

  it("disconnects the MutationObserver on unmount", () => {
    // Spy on MutationObserver.prototype.disconnect to verify cleanup. Without
    // it, a long-lived observer keeps the unmounted hook alive (a leak on
    // every theme-aware route mount/unmount cycle).
    const spy = vi.spyOn(MutationObserver.prototype, "disconnect");
    const { unmount } = renderHook(() => useResolvedTheme());
    unmount();
    expect(spy).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });
});
