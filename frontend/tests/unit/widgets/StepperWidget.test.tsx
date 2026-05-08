import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { StepperWidget } from "@/components/forms/widgets/StepperWidget";

const baseProps = {
  id: "n",
  name: "n",
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

  it("does not refill input when user clears it", async () => {
    const onChange = vi.fn();
    function Wrapper() {
      const [v, setV] = React.useState<number | undefined>(100);
      return (
        <StepperWidget
          {...baseProps}
          value={v}
          onChange={(next: number | undefined) => {
            onChange(next);
            setV(next);
          }}
        />
      );
    }
    render(<Wrapper />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    // After clear, input should be empty — NOT snapped back to "1" (min)
    expect(input.value).toBe("");
  });
});
