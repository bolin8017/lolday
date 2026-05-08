import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { RangeSliderWidget } from "@/components/forms/widgets/RangeSliderWidget";

const baseProps = {
  id: "test_id",
  name: "test_id",
  label: "test",
  schema: { type: "number", minimum: 0, maximum: 1, default: 0.5 } as const,
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

describe("RangeSliderWidget", () => {
  it("renders a slider and a numeric input that share the value", () => {
    render(
      <RangeSliderWidget {...baseProps} value={0.7} onChange={() => {}} />,
    );
    const numInput = screen.getByRole("spinbutton");
    expect(numInput).toHaveValue(0.7);
    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("aria-valuenow", "0.7");
  });

  it("calls onChange when the numeric input changes", async () => {
    const onChange = vi.fn();
    render(
      <RangeSliderWidget {...baseProps} value={0.5} onChange={onChange} />,
    );
    const numInput = screen.getByRole("spinbutton");
    await userEvent.clear(numInput);
    await userEvent.type(numInput, "0.3");
    expect(onChange).toHaveBeenLastCalledWith(0.3);
  });

  it("respects schema minimum / maximum on the slider", () => {
    render(
      <RangeSliderWidget {...baseProps} value={0.5} onChange={() => {}} />,
    );
    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("aria-valuemin", "0");
    expect(slider).toHaveAttribute("aria-valuemax", "1");
  });

  it("does not clobber in-progress edits when parent echoes the same numeric value", async () => {
    // Regression: the prop-sync effect previously re-ran whenever propNumeric
    // changed, including the round-trip from the widget's own onChange call.
    // When a user backspaces from "0.5" to "0." the draft parses as 0; if
    // parent echoes 0, a naive effect would overwrite "0." → "0".
    //
    // jsdom normalises input[type=number] .value (e.g. "0." → "0"), so we
    // test the equivalent round-trip: type "0" (numeric 0), then verify
    // propNumeric echoing 0 back does NOT trigger a second setDraft call.
    const onChangeSpy = vi.fn();
    let externalSetValue: (v: number | undefined) => void = () => {};

    function Wrapper() {
      const [v, setV] = React.useState<number | undefined>(0.5);
      externalSetValue = setV;
      return (
        <RangeSliderWidget
          {...baseProps}
          value={v}
          onChange={(next: number | undefined) => {
            onChangeSpy(next);
            setV(next);
          }}
        />
      );
    }
    render(<Wrapper />);
    const numInput = screen.getByRole("spinbutton") as HTMLInputElement;
    // Type 0 — this triggers onChange(0), setV(0), propNumeric=0
    await userEvent.clear(numInput);
    await userEvent.type(numInput, "0");
    expect(numInput).toHaveValue(0);
    // Now externally echo the same value (simulates parent re-render with
    // the same prop). The input must NOT be overwritten.
    const callCountBefore = onChangeSpy.mock.calls.length;
    act(() => externalSetValue(0));
    // onChange should not have fired again from the effect
    expect(onChangeSpy.mock.calls.length).toBe(callCountBefore);
    expect(numInput).toHaveValue(0);
  });
});
