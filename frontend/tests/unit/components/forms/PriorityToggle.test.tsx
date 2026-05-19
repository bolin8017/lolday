import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PriorityToggle } from "@/components/forms/PriorityToggle";

describe("PriorityToggle", () => {
  it("renders two buttons inside a labelled group (admin-only priority bump)", () => {
    render(<PriorityToggle value={0} onChange={() => {}} />);
    // role="group" with aria-label="..." — gives axe a structural label.
    expect(screen.getByRole("group")).toHaveAttribute("aria-label");
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("aria-pressed mirrors the current value (normal=value 0, high=value 1)", () => {
    const { rerender } = render(
      <PriorityToggle value={0} onChange={() => {}} />,
    );
    const buttons = screen.getAllByRole("button");
    // First button = normal, second = high.
    expect(buttons[0]).toHaveAttribute("aria-pressed", "true");
    expect(buttons[1]).toHaveAttribute("aria-pressed", "false");

    rerender(<PriorityToggle value={1} onChange={() => {}} />);
    const buttonsHigh = screen.getAllByRole("button");
    expect(buttonsHigh[0]).toHaveAttribute("aria-pressed", "false");
    expect(buttonsHigh[1]).toHaveAttribute("aria-pressed", "true");
  });

  it("clicking the inactive option fires onChange with the new value", async () => {
    const onChange = vi.fn();
    render(<PriorityToggle value={0} onChange={onChange} />);
    const [normalBtn, highBtn] = screen.getAllByRole("button");
    // Click the "high" button; onChange must be called with 1.
    await userEvent.click(highBtn);
    expect(onChange).toHaveBeenCalledExactlyOnceWith(1);

    // Sanity: clicking the already-selected normal button does NOT fire
    // (component short-circuits when next === current).
    await userEvent.click(normalBtn);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("does not fire onChange when clicking the already-active option", async () => {
    const onChange = vi.fn();
    render(<PriorityToggle value={1} onChange={onChange} />);
    const [, highBtn] = screen.getAllByRole("button");
    await userEvent.click(highBtn);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("disabled=true blocks the click and visually dims the group", async () => {
    const onChange = vi.fn();
    render(<PriorityToggle value={0} onChange={onChange} disabled />);
    const [, highBtn] = screen.getAllByRole("button");
    expect(highBtn).toBeDisabled();
    // The wrapper picks up opacity-60 so visual state matches the disabled
    // semantic state — pin so a refactor doesn't silently un-grey it.
    expect(screen.getByRole("group").className).toMatch(/opacity-60/);
    await userEvent.click(highBtn);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("both buttons declare type='button' (never accidentally submits a parent form)", () => {
    render(<PriorityToggle value={0} onChange={() => {}} />);
    for (const btn of screen.getAllByRole("button")) {
      expect(btn).toHaveAttribute("type", "button");
    }
  });
});
