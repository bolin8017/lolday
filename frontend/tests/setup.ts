import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Register the i18next instance globally so components calling useTranslation
// (StatusBadge, OpenInMlflowButton, …) get a real `t` function instead of
// crashing with NO_I18NEXT_INSTANCE / `i18n.exists is not a function`.
import "@/i18n";

// Radix UI primitives use PointerEvent + pointer capture APIs that jsdom does not
// implement. See radix-ui/primitives#1342.
window.PointerEvent = MouseEvent as typeof PointerEvent;
window.HTMLElement.prototype.hasPointerCapture = () => false; // jsdom: no pointer capture; Radix only checks the boolean.
window.HTMLElement.prototype.releasePointerCapture = () => {};
window.HTMLElement.prototype.setPointerCapture = () => {};

// jsdom does not implement scrollIntoView. Stubbed so components that auto-scroll
// (e.g. focused list items, command palettes) do not throw "not implemented" warnings.
window.HTMLElement.prototype.scrollIntoView = () => {};

afterEach(() => cleanup());
