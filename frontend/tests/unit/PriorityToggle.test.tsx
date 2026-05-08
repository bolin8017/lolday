import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { PriorityToggle } from "@/components/forms/PriorityToggle";

describe("PriorityToggle", () => {
  it("renders Normal and Priority buttons", () => {
    render(<PriorityToggle value={0} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /normal/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /priority/i }),
    ).toBeInTheDocument();
  });

  it("marks Normal as pressed when value=0", () => {
    render(<PriorityToggle value={0} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /normal/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /priority/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("marks Priority as pressed when value=1", () => {
    render(<PriorityToggle value={1} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /priority/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /normal/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("calls onChange(1) when Priority is clicked from value=0", async () => {
    const onChange = vi.fn();
    render(<PriorityToggle value={0} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /priority/i }));
    expect(onChange).toHaveBeenCalledWith(1);
  });

  it("calls onChange(0) when Normal is clicked from value=1", async () => {
    const onChange = vi.fn();
    render(<PriorityToggle value={1} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /normal/i }));
    expect(onChange).toHaveBeenCalledWith(0);
  });

  it("does not fire onChange when clicking the already-active button", async () => {
    const onChange = vi.fn();
    render(<PriorityToggle value={0} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /normal/i }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("disables both buttons when disabled prop is set", () => {
    render(<PriorityToggle value={0} onChange={() => {}} disabled />);
    expect(screen.getByRole("button", { name: /normal/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /priority/i })).toBeDisabled();
  });
});
