import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { StepperWidget } from "@/components/forms/widgets/StepperWidget";

const baseProps = {
  id: "n",
  label: "n",
  schema: { type: "integer", minimum: 1, default: 100 } as const,
  uiSchema: {},
  options: {},
  formContext: {},
  registry: {} as never,
  onBlur: () => {},
  onFocus: () => {},
  required: false,
  disabled: false,
  readonly: false,
  rawErrors: [] as string[],
  multiple: false,
  hideError: false,
};

describe("StepperWidget", () => {
  it("renders − value + buttons and an input", () => {
    render(<StepperWidget {...baseProps} value={100} onChange={() => {}} />);
    expect(
      screen.getByRole("button", { name: /decrement/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /increment/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("spinbutton")).toHaveValue(100);
  });

  it("increments by 1 on + click", async () => {
    const onChange = vi.fn();
    render(<StepperWidget {...baseProps} value={100} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /increment/i }));
    expect(onChange).toHaveBeenCalledWith(101);
  });

  it("decrements by 1 on − click but never below schema.minimum", async () => {
    const onChange = vi.fn();
    render(<StepperWidget {...baseProps} value={1} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /decrement/i }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
