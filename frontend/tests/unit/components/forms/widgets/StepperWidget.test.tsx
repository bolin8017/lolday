import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { WidgetProps } from "@rjsf/utils";
import { StepperWidget } from "@/components/forms/widgets/StepperWidget";

/**
 * ``StepperWidget`` is the RJSF widget mapping for bounded integer-ish
 * fields rendered as a − / + button pair around a numeric input. The
 * existing test under ``tests/unit/widgets/StepperWidget.test.tsx``
 * covers the basic render, the simple increment/decrement, the
 * min-boundary, and the clear-input regression. This file fills the
 * gaps surfaced during the system-review loop:
 *
 * - ``multipleOf`` becomes the ``step`` attribute on the input AND the
 *   bump delta (single increment moves by step, not by 1)
 * - max boundary disables the increment button (``cantInc``)
 * - ``disabled`` propagates to both buttons + input
 * - ``readonly`` propagates to both buttons + input
 * - ``id`` passes through to the input element
 * - draft string resets to the new prop when value changes externally
 * - NaN-producing input (e.g. ``"abc"``) normalises to ``undefined``
 */

function makeProps(over: Partial<WidgetProps> = {}): WidgetProps {
  return {
    id: "root_epochs",
    name: "epochs",
    value: 5,
    onChange: vi.fn(),
    onBlur: vi.fn(),
    onFocus: vi.fn(),
    schema: {
      type: "integer",
      minimum: 1,
      maximum: 10,
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

describe("StepperWidget", () => {
  it("forwards multipleOf as the step attribute on the input", () => {
    render(
      <StepperWidget
        {...makeProps({
          schema: {
            type: "number",
            minimum: 0,
            maximum: 1,
            multipleOf: 0.25,
          } as WidgetProps["schema"],
          value: 0.25,
        })}
      />,
    );
    expect(screen.getByRole("spinbutton")).toHaveAttribute("step", "0.25");
  });

  it("bumps by multipleOf instead of 1 when schema.multipleOf is set", async () => {
    const onChange = vi.fn();
    render(
      <StepperWidget
        {...makeProps({
          schema: {
            type: "number",
            minimum: 0,
            maximum: 1,
            multipleOf: 0.1,
          } as WidgetProps["schema"],
          value: 0.5,
          onChange,
        })}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /increment/i }));
    // 0.5 + 0.1 is the canonical 0.6000000000000001 IEEE-754 result; assert
    // close-enough so the test doesn't fight floating-point precision.
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toBeCloseTo(0.6, 9);
  });

  it("disables the increment button at the schema maximum", async () => {
    const onChange = vi.fn();
    render(<StepperWidget {...makeProps({ value: 10, onChange })} />);
    const inc = screen.getByRole("button", { name: /increment/i });
    expect(inc).toBeDisabled();
    await userEvent.click(inc);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("disables both buttons + input when disabled=true", () => {
    render(<StepperWidget {...makeProps({ disabled: true })} />);
    expect(screen.getByRole("button", { name: /decrement/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /increment/i })).toBeDisabled();
    expect(screen.getByRole("spinbutton")).toBeDisabled();
  });

  it("disables both buttons + input when readonly=true", () => {
    render(<StepperWidget {...makeProps({ readonly: true })} />);
    expect(screen.getByRole("button", { name: /decrement/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /increment/i })).toBeDisabled();
    expect(screen.getByRole("spinbutton")).toBeDisabled();
  });

  it("propagates id to the input element", () => {
    render(<StepperWidget {...makeProps({ id: "root_lr_steps" })} />);
    expect(screen.getByRole("spinbutton")).toHaveAttribute(
      "id",
      "root_lr_steps",
    );
  });

  it("syncs the draft when the value prop changes externally", () => {
    const { rerender } = render(<StepperWidget {...makeProps({ value: 3 })} />);
    expect(screen.getByRole("spinbutton")).toHaveValue(3);
    rerender(<StepperWidget {...makeProps({ value: 7 })} />);
    expect(screen.getByRole("spinbutton")).toHaveValue(7);
  });

  it("calls onChange(undefined) when a non-numeric string is typed", () => {
    const onChange = vi.fn();
    render(<StepperWidget {...makeProps({ value: 5, onChange })} />);
    // ``type="number"`` inputs in jsdom let us shove a non-numeric string
    // through ``fireEvent.change``; the widget's handleInput must catch
    // the NaN coercion and forward ``undefined`` to the form.
    fireEvent.change(screen.getByRole("spinbutton"), {
      target: { value: "abc" },
    });
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });
});
