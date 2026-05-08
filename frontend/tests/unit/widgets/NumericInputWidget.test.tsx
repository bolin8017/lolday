import React from "react";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { NumericInputWidget } from "@/components/forms/widgets/NumericInputWidget";

const baseProps = {
  id: "lr",
  name: "lr",
  label: "lr",
  schema: {
    type: "number",
    exclusiveMinimum: 0,
    default: 0.001,
  } as const,
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

describe("NumericInputWidget", () => {
  it("renders a numeric input with the given value", () => {
    render(
      <NumericInputWidget {...baseProps} value={0.001} onChange={() => {}} />,
    );
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    expect(input.value).toBe("0.001");
  });

  it("calls onChange with parsed number on input", async () => {
    const onChange = vi.fn();
    render(
      <NumericInputWidget {...baseProps} value={0.001} onChange={onChange} />,
    );
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "0.5");
    expect(onChange).toHaveBeenLastCalledWith(0.5);
  });

  it("does not refill draft when parent echoes the same value back", () => {
    // Regression: when user types "0.5" → onChange(0.5) → parent re-renders
    // with value=0.5 → effect should NOT clobber draft "0.5"
    const onChange = vi.fn();
    function Wrapper() {
      const [v, setV] = React.useState<number | undefined>(0.001);
      return (
        <NumericInputWidget
          {...baseProps}
          value={v}
          onChange={(next: number | undefined) => {
            onChange(next);
            setV(next);
          }}
        />
      );
    }
    const { rerender } = render(<Wrapper />);
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    // Simulate the round-trip: parent gives same value back
    act(() => {
      rerender(<Wrapper />);
    });
    expect(input.value).toBe("0.001");
  });
});
