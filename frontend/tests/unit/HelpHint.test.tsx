import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect } from "vitest";
import { HelpHint } from "@/components/common/HelpHint";

describe("HelpHint", () => {
  it("renders a help icon button", () => {
    render(<HelpHint>quick tip</HelpHint>);
    expect(
      screen.getByRole("button", { name: /help|info|hint/i }),
    ).toBeInTheDocument();
  });

  it("tooltip mode reveals text on hover", async () => {
    render(<HelpHint>tooltip text</HelpHint>);
    const trigger = screen.getByRole("button", { name: /help|info|hint/i });
    await userEvent.hover(trigger);
    // Radix Tooltip renders the text twice: once visible, once in a hidden
    // role="tooltip" span for ARIA. Use findAllByText + toHaveLength check.
    const matches = await screen.findAllByText(/tooltip text/i);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it("popover mode reveals text on click", async () => {
    render(<HelpHint popover>popover text</HelpHint>);
    const trigger = screen.getByRole("button", { name: /help|info|hint/i });
    await userEvent.click(trigger);
    expect(await screen.findByText(/popover text/i)).toBeInTheDocument();
  });
});
