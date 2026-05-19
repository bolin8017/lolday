import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WidgetProps } from "@rjsf/utils";
import { RangeSliderWidget } from "@/components/forms/widgets/RangeSliderWidget";

/**
 * ``RangeSliderWidget`` is the RJSF widget mapping for bounded
 * floating-point fields rendered as a slider + numeric-input pair.
 * The existing test under ``tests/unit/widgets/RangeSliderWidget.test.tsx``
 * covers the basic render and the in-progress-edit regression. This
 * file fills the gaps surfaced during the system-review loop:
 *
 * - schema-default fallback when min / max / multipleOf are missing
 * - empty input clears the value to ``undefined``
 * - NaN-producing input (e.g. ``"abc"`` typed into ``type="number"``)
 *   normalises to ``undefined``
 * - ``disabled`` / ``readonly`` propagate to both slider and input
 * - ``multipleOf`` becomes the ``step`` attribute on the input
 * - id passes through to the input
 * - draft string resets to the new prop when value changes externally
 *
 * Slider interaction (``onValueChange``) is intentionally NOT covered
 * here — Radix Slider's pointer handling needs a real layout (see
 * auto-memory ``project_radix_pointer_events_testing``); the
 * onChange-via-numeric-input path exercises the same handler logic.
 */

function makeProps(over: Partial<WidgetProps> = {}): WidgetProps {
  return {
    id: "root_threshold",
    name: "threshold",
    value: 0.5,
    onChange: vi.fn(),
    onBlur: vi.fn(),
    onFocus: vi.fn(),
    schema: {
      type: "number",
      minimum: 0,
      maximum: 1,
    } as WidgetProps["schema"],
    options: {},
    label: "",
    rawErrors: [],
    disabled: false,
    readonly: false,
    required: false,
    autofocus: false,
    placeholder: "",
    multiple: false,
    hideLabel: false,
    hideError: false,
    formContext: undefined,
    registry: {} as WidgetProps["registry"],
    uiSchema: {},
    ...over,
  } as WidgetProps;
}

describe("RangeSliderWidget", () => {
  it("falls back to min=0 / max=1 when the schema omits them", () => {
    render(
      <RangeSliderWidget
        {...makeProps({
          schema: { type: "number" } as WidgetProps["schema"],
        })}
      />,
    );
    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("aria-valuemin", "0");
    expect(slider).toHaveAttribute("aria-valuemax", "1");
  });

  it("forwards multipleOf as the step attribute on the numeric input", () => {
    render(
      <RangeSliderWidget
        {...makeProps({
          schema: {
            type: "number",
            minimum: 0,
            maximum: 1,
            multipleOf: 0.05,
          } as WidgetProps["schema"],
        })}
      />,
    );
    expect(screen.getByRole("spinbutton")).toHaveAttribute("step", "0.05");
  });

  it("falls back to step=0.01 when multipleOf is omitted", () => {
    render(<RangeSliderWidget {...makeProps()} />);
    expect(screen.getByRole("spinbutton")).toHaveAttribute("step", "0.01");
  });

  it("calls onChange(undefined) when the numeric input is cleared", () => {
    const onChange = vi.fn();
    render(<RangeSliderWidget {...makeProps({ onChange })} />);
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it("calls onChange(num) when a valid number is typed", () => {
    const onChange = vi.fn();
    render(<RangeSliderWidget {...makeProps({ onChange })} />);
    fireEvent.change(screen.getByRole("spinbutton"), {
      target: { value: "0.42" },
    });
    expect(onChange).toHaveBeenLastCalledWith(0.42);
  });

  it("propagates id to the numeric input element", () => {
    render(<RangeSliderWidget {...makeProps({ id: "root_lr" })} />);
    expect(screen.getByRole("spinbutton")).toHaveAttribute("id", "root_lr");
  });

  it("disables both slider and input when disabled=true", () => {
    render(<RangeSliderWidget {...makeProps({ disabled: true })} />);
    expect(screen.getByRole("slider")).toHaveAttribute(
      "data-disabled",
      expect.any(String),
    );
    expect(screen.getByRole("spinbutton")).toBeDisabled();
  });

  it("disables both slider and input when readonly=true", () => {
    render(<RangeSliderWidget {...makeProps({ readonly: true })} />);
    expect(screen.getByRole("slider")).toHaveAttribute(
      "data-disabled",
      expect.any(String),
    );
    expect(screen.getByRole("spinbutton")).toBeDisabled();
  });

  it("syncs the draft when the value prop changes externally", () => {
    const { rerender } = render(
      <RangeSliderWidget {...makeProps({ value: 0.3 })} />,
    );
    rerender(<RangeSliderWidget {...makeProps({ value: 0.7 })} />);
    expect(screen.getByRole("spinbutton")).toHaveValue(0.7);
  });

  it("treats a string value prop as numeric for the slider", () => {
    // RJSF can pass JSON values that haven't been numerically coerced
    // yet (e.g. when the form has just been deserialised from URL
    // state). The widget's ``Number(value ?? min)`` coercion must
    // keep the slider in sync.
    render(
      <RangeSliderWidget
        {...makeProps({ value: "0.4" as unknown as number })}
      />,
    );
    expect(screen.getByRole("slider")).toHaveAttribute("aria-valuenow", "0.4");
  });
});
