import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Register the i18next instance globally so components calling useTranslation
// (StatusBadge, OpenInMlflowButton, …) get a real `t` function instead of
// crashing with NO_I18NEXT_INSTANCE / `i18n.exists is not a function`.
import "@/i18n";

// Radix UI primitives use PointerEvent + pointer capture APIs that jsdom does not
// implement. See radix-ui/primitives#1342.
window.PointerEvent = MouseEvent as typeof PointerEvent;

// @radix-ui/react-use-size (used by Slider thumb sizing) calls ResizeObserver
// which jsdom does not implement. Stub with a no-op observer.
window.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};
window.HTMLElement.prototype.hasPointerCapture = () => false; // jsdom: no pointer capture; Radix only checks the boolean.
window.HTMLElement.prototype.releasePointerCapture = () => {};
window.HTMLElement.prototype.setPointerCapture = () => {};

// jsdom does not implement scrollIntoView. Stubbed so components that auto-scroll
// (e.g. focused list items, command palettes) do not throw "not implemented" warnings.
window.HTMLElement.prototype.scrollIntoView = () => {};

// jsdom does not implement window.matchMedia. Stubbed globally so any component
// (Sidebar block, ThemeProvider, useIsMobile, …) that reads matchMedia on mount
// does not throw "not a function". Individual tests may override with
// Object.defineProperty(window, "matchMedia", { configurable: true, value: … })
// when they need custom behaviour (e.g. ThemeProvider OS-pref change test).
Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: vi.fn().mockReturnValue({
    matches: false,
    media: "",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: () => true,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
  }),
});

afterEach(() => cleanup());
