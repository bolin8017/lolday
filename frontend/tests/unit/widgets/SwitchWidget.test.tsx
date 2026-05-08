import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { SwitchWidget } from "@/components/forms/widgets/SwitchWidget";

const baseProps = {
  id: "x",
  name: "x",
  label: "x",
  schema: { type: "boolean", default: false } as const,
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

describe("SwitchWidget", () => {
  it("renders a switch in the value state", () => {
    render(<SwitchWidget {...baseProps} value={false} onChange={() => {}} />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("toggles via click", async () => {
    const onChange = vi.fn();
    render(<SwitchWidget {...baseProps} value={false} onChange={onChange} />);
    await userEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
