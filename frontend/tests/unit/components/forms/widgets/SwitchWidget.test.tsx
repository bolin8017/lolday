import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { WidgetProps } from "@rjsf/utils";
import { SwitchWidget } from "@/components/forms/widgets/SwitchWidget";

/**
 * ``SwitchWidget`` is the RJSF widget mapping for boolean fields with
 * UI hint ``ui:widget = "switch"``. Wraps the shadcn ``Switch`` and
 * forwards the RJSF contract:
 *
 * - ``checked`` mirrors ``!!value`` (truthy/falsy coercion).
 * - ``onCheckedChange(c)`` calls RJSF ``onChange(c)`` (passing the bool
 *   through unchanged).
 * - ``disabled`` is the OR of RJSF ``disabled`` and ``readonly``.
 * - ``id`` is forwarded to the DOM element (so RJSF's ``label[for=id]``
 *   matches).
 */

function makeProps(over: Partial<WidgetProps> = {}): WidgetProps {
  return {
    id: "root_enableX",
    name: "enableX",
    value: false,
    onChange: vi.fn(),
    onBlur: vi.fn(),
    onFocus: vi.fn(),
    schema: {} as WidgetProps["schema"],
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

describe("SwitchWidget", () => {
  it("renders unchecked when value is false/undefined", () => {
    render(<SwitchWidget {...makeProps({ value: false })} />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("renders checked when value is true", () => {
    render(<SwitchWidget {...makeProps({ value: true })} />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("treats any truthy value as checked", () => {
    // RJSF generally passes booleans, but the widget coerces via `!!value`
    // — defensive: a non-boolean truthy value should still render checked.
    render(<SwitchWidget {...makeProps({ value: 1 as unknown as boolean })} />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("forwards id to the rendered switch", () => {
    render(<SwitchWidget {...makeProps({ id: "root_myField" })} />);
    expect(screen.getByRole("switch")).toHaveAttribute("id", "root_myField");
  });

  it("fires onChange with the new boolean when toggled", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<SwitchWidget {...makeProps({ value: false, onChange })} />);
    await user.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("disables when RJSF disabled=true", () => {
    render(<SwitchWidget {...makeProps({ disabled: true })} />);
    expect(screen.getByRole("switch")).toBeDisabled();
  });

  it("disables when RJSF readonly=true", () => {
    render(<SwitchWidget {...makeProps({ readonly: true })} />);
    expect(screen.getByRole("switch")).toBeDisabled();
  });
});
