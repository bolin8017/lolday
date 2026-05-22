import { render, act } from "@testing-library/react";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { ThemeProvider, useTheme } from "@/components/ThemeProvider";

const STORAGE_KEY = "lolday-theme";

function ThemeReporter() {
  const { theme, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="t">{theme}</span>
      <button data-testid="dark" onClick={() => setTheme("dark")}>
        dark
      </button>
      <button data-testid="light" onClick={() => setTheme("light")}>
        light
      </button>
      <button data-testid="system" onClick={() => setTheme("system")}>
        system
      </button>
    </div>
  );
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("light", "dark");
  });

  it("uses default theme when localStorage is empty", () => {
    const { getByTestId } = render(
      <ThemeProvider defaultTheme="system" storageKey={STORAGE_KEY}>
        <ThemeReporter />
      </ThemeProvider>,
    );
    expect(getByTestId("t").textContent).toBe("system");
  });

  it("loads persisted theme from localStorage", () => {
    localStorage.setItem(STORAGE_KEY, "dark");
    const { getByTestId } = render(
      <ThemeProvider defaultTheme="system" storageKey={STORAGE_KEY}>
        <ThemeReporter />
      </ThemeProvider>,
    );
    expect(getByTestId("t").textContent).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("setTheme writes localStorage and toggles <html> class", () => {
    const { getByTestId } = render(
      <ThemeProvider defaultTheme="light" storageKey={STORAGE_KEY}>
        <ThemeReporter />
      </ThemeProvider>,
    );
    act(() => getByTestId("dark").click());
    expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("light")).toBe(false);

    act(() => getByTestId("light").click());
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("ignores invalid stored values and falls back to defaultTheme", () => {
    localStorage.setItem(STORAGE_KEY, "neon-magenta");
    const { getByTestId } = render(
      <ThemeProvider defaultTheme="light" storageKey={STORAGE_KEY}>
        <ThemeReporter />
      </ThemeProvider>,
    );
    expect(getByTestId("t").textContent).toBe("light");
  });

  it("keeps the in-memory toggle working when localStorage.setItem throws", () => {
    // Safari Private mode / storage-quota-exceeded both throw on setItem.
    // The catch block in setTheme must NOT swallow the state update — the
    // user is otherwise locked out of theme switching.
    const setItemSpy = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("QuotaExceededError");
      });
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const { getByTestId } = render(
      <ThemeProvider defaultTheme="light" storageKey={STORAGE_KEY}>
        <ThemeReporter />
      </ThemeProvider>,
    );
    act(() => getByTestId("dark").click());
    expect(getByTestId("t").textContent).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(warnSpy).toHaveBeenCalledWith(
      "ThemeProvider: localStorage.setItem failed",
      expect.any(Error),
    );
    setItemSpy.mockRestore();
    warnSpy.mockRestore();
  });

  it("useTheme throws when called outside ThemeProvider", () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    function Bare() {
      useTheme();
      return null;
    }
    expect(() => render(<Bare />)).toThrow(
      /useTheme must be used within <ThemeProvider>/,
    );
    errSpy.mockRestore();
  });

  it("system mode applies <html class='dark'> when OS prefers dark on mount", () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: true, // prefers-color-scheme: dark
        media: "(prefers-color-scheme: dark)",
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: () => true,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    });
    render(
      <ThemeProvider defaultTheme="system" storageKey={STORAGE_KEY}>
        <ThemeReporter />
      </ThemeProvider>,
    );
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("light")).toBe(false);
  });

  it("system mode re-applies <html> class when OS preference changes", () => {
    type PrefListener = (e: MediaQueryListEvent) => void;
    const listeners: PrefListener[] = [];
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: false,
        media: "(prefers-color-scheme: dark)",
        addEventListener: (_: string, cb: PrefListener) => listeners.push(cb),
        removeEventListener: vi.fn(),
        dispatchEvent: () => true,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    });

    render(
      <ThemeProvider defaultTheme="system" storageKey={STORAGE_KEY}>
        <ThemeReporter />
      </ThemeProvider>,
    );
    expect(document.documentElement.classList.contains("light")).toBe(true);

    act(() => {
      listeners.forEach((cb) => cb({ matches: true } as MediaQueryListEvent));
    });
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("light")).toBe(false);

    // Cross back to light to exercise the ternary's `light` branch.
    act(() => {
      listeners.forEach((cb) => cb({ matches: false } as MediaQueryListEvent));
    });
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });
});
