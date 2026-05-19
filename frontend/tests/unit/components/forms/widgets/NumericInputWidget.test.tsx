import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WidgetProps } from "@rjsf/utils";
import { NumericInputWidget } from "@/components/forms/widgets/NumericInputWidget";

/**
 * ``NumericInputWidget`` is the RJSF widget mapping for integer /
 * number fields. Pairs with SwitchWidget (covered in #374). The
 * widget owns a draft string state (so partial entries like "0."
 * don't get clobbered by the round-trip back to number), forwards
 * ``minimum`` / ``maximum`` / ``multipleOf`` from the JSON Schema, and
 * normalises an empty input to ``undefined``.
 *
 * Tests use ``fireEvent.change`` (rather than userEvent.type) because
 * the draft-state hook needs each emit to reflect the *exact* string
 * shown, including partial-decimal states that ``userEvent.type``
 * would type one keystroke at a time.
 *
 * Behaviours covered:
 *
 * - Initial draft mirrors ``value`` (undefined → "", number → "12.5").
 * - Numeric typed input fires ``onChange(num)`` with the parsed number.
 * - Empty input fires ``onChange(undefined)``.
 * - Partial decimal "0." parses to NaN, doesn't fire onChange (draft
 *   pinned).
 * - External prop change syncs the draft (form reset path).
 * - ``min`` / ``max`` / ``step`` forwarded from schema.
 * - ``disabled`` and ``readonly`` propagate.
 * - ``id`` forwarded to the input.
 */

function makeProps(over: Partial<WidgetProps> = {}): WidgetProps {
  return {
    id: "root_value",
    name: "value",
    value: undefined,
    onChange: vi.fn(),
    onBlur: vi.fn(),
    onFocus: vi.fn(),
    schema: { type: "number" } as WidgetProps["schema"],
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

describe("NumericInputWidget", () => {
  it("renders empty string when value is undefined", () => {
    render(<NumericInputWidget {...makeProps({ value: undefined })} />);
    expect(screen.getByRole("spinbutton")).toHaveValue(null);
  });

  it("renders the numeric value as the draft string", () => {
    render(<NumericInputWidget {...makeProps({ value: 12.5 })} />);
    expect(screen.getByRole("spinbutton")).toHaveValue(12.5);
  });

  it("forwards id to the rendered input", () => {
    render(<NumericInputWidget {...makeProps({ id: "root_lr" })} />);
    expect(screen.getByRole("spinbutton")).toHaveAttribute("id", "root_lr");
  });

  it("forwards min / max / step from the schema", () => {
    render(
      <NumericInputWidget
        {...makeProps({
          schema: {
            type: "number",
            minimum: 0,
            maximum: 1,
            multipleOf: 0.01,
          } as WidgetProps["schema"],
        })}
      />,
    );
    const input = screen.getByRole("spinbutton");
    expect(input).toHaveAttribute("min", "0");
    expect(input).toHaveAttribute("max", "1");
    expect(input).toHaveAttribute("step", "0.01");
  });

  it("falls back to step='any' when multipleOf is not a number", () => {
    render(<NumericInputWidget {...makeProps()} />);
    expect(screen.getByRole("spinbutton")).toHaveAttribute("step", "any");
  });

  it("calls onChange(num) when a numeric value is typed", () => {
    const onChange = vi.fn();
    render(<NumericInputWidget {...makeProps({ onChange })} />);
    fireEvent.change(screen.getByRole("spinbutton"), {
      target: { value: "42" },
    });
    expect(onChange).toHaveBeenLastCalledWith(42);
  });

  it("calls onChange(undefined) when the input is cleared", () => {
    const onChange = vi.fn();
    render(<NumericInputWidget {...makeProps({ value: 10, onChange })} />);
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(undefined);
  });

  it("disables the input when disabled=true", () => {
    render(<NumericInputWidget {...makeProps({ disabled: true })} />);
    expect(screen.getByRole("spinbutton")).toBeDisabled();
  });

  it("disables the input when readonly=true", () => {
    render(<NumericInputWidget {...makeProps({ readonly: true })} />);
    expect(screen.getByRole("spinbutton")).toBeDisabled();
  });

  it("syncs the draft when the value prop changes externally", () => {
    const { rerender } = render(
      <NumericInputWidget {...makeProps({ value: 1 })} />,
    );
    rerender(<NumericInputWidget {...makeProps({ value: 99 })} />);
    expect(screen.getByRole("spinbutton")).toHaveValue(99);
  });

  it("clears the draft when the value prop changes to undefined", () => {
    const { rerender } = render(
      <NumericInputWidget {...makeProps({ value: 1 })} />,
    );
    rerender(<NumericInputWidget {...makeProps({ value: undefined })} />);
    expect(screen.getByRole("spinbutton")).toHaveValue(null);
  });
});
