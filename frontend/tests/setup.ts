import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Register the i18next instance globally so components calling useTranslation
// (StatusBadge, OpenInMlflowButton, …) get a real `t` function instead of
// crashing with NO_I18NEXT_INSTANCE / `i18n.exists is not a function`.
import "@/i18n";

// Radix UI primitives (DropdownMenu, Dialog, Popover …) rely on PointerEvent
// and pointer-capture APIs that jsdom does not implement. Alias them to
// MouseEvent so that fireEvent.click correctly opens/closes Radix portals.
// Reference: https://github.com/radix-ui/primitives/issues/1342
window.PointerEvent = MouseEvent as typeof PointerEvent;
window.HTMLElement.prototype.scrollIntoView = () => {};
window.HTMLElement.prototype.hasPointerCapture = () => false;
window.HTMLElement.prototype.releasePointerCapture = () => {};
window.HTMLElement.prototype.setPointerCapture = () => {};

afterEach(() => cleanup());
