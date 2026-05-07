import { renderHook, act, waitFor } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { useResolvedTheme } from "@/hooks/useResolvedTheme";

describe("useResolvedTheme", () => {
  beforeEach(() => {
    document.documentElement.classList.remove("light", "dark");
  });

  it("returns 'dark' when <html> has dark class", () => {
    document.documentElement.classList.add("dark");
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("dark");
  });

  it("returns 'light' when <html> has light class", () => {
    document.documentElement.classList.add("light");
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
  });

  it("falls back to matchMedia when neither class is set", () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: true, // simulate prefers dark
        media: "(prefers-color-scheme: dark)",
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    });
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("dark");
  });

  it("updates when documentElement class flips", async () => {
    document.documentElement.classList.add("light");
    const { result } = renderHook(() => useResolvedTheme());
    expect(result.current).toBe("light");
    act(() => {
      document.documentElement.classList.remove("light");
      document.documentElement.classList.add("dark");
    });
    await waitFor(() => expect(result.current).toBe("dark"));
  });
});
