import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HelpHint } from "@/components/common/HelpHint";

describe("HelpHint", () => {
  it("renders an accessible help button (default = Tooltip variant)", () => {
    render(<HelpHint>need-help</HelpHint>);
    const btn = screen.getByRole("button", { name: "Help" });
    expect(btn).toBeInTheDocument();
    // Default size class — h-6 w-6 (tiny "?" icon).
    expect(btn.className).toMatch(/h-6/);
    expect(btn.className).toMatch(/w-6/);
  });

  it("renders the Popover variant when popover=true", () => {
    render(<HelpHint popover>long help content</HelpHint>);
    expect(screen.getByRole("button", { name: "Help" })).toBeInTheDocument();
  });

  it("respects the className override (sizes the icon container differently)", () => {
    render(<HelpHint className="h-8 w-8 custom">x</HelpHint>);
    const btn = screen.getByRole("button", { name: "Help" });
    expect(btn.className).toMatch(/h-8/);
    expect(btn.className).toMatch(/w-8/);
    expect(btn.className).toMatch(/custom/);
    // Default sizing must NOT also be applied — the override fully replaces.
    expect(btn.className).not.toMatch(/h-6/);
  });

  it("never declares a default form-submission action (type='button')", () => {
    // Important when embedded inside a <form> with RJSF — a default
    // submit-button would close the surrounding form mid-tooltip-hover.
    render(<HelpHint>hint</HelpHint>);
    expect(screen.getByRole("button", { name: "Help" })).toHaveAttribute(
      "type",
      "button",
    );
  });
});
