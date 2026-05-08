import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { RangeSliderWidget } from "@/components/forms/widgets/RangeSliderWidget";

const baseProps = {
  id: "test_id",
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
});
