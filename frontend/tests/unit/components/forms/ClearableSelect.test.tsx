import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ClearableSelect } from "@/components/forms/ClearableSelect";

/**
 * ``ClearableSelect`` wraps shadcn ``Select`` and adds a "Clear" (X)
 * button when the field has a value and ``clearable`` is true. Used
 * for optional fields where Radix Select alone has no way to
 * deselect.
 *
 * Behaviours covered:
 *
 * - Clear button is hidden when ``clearable`` is false (default).
 * - Clear button is hidden when ``clearable`` is true but ``value`` is
 *   empty (showClear gates on truthy value).
 * - Clear button is hidden when disabled, even if ``value`` is set.
 * - Clear button is shown when clearable + truthy value + not disabled.
 * - Clicking Clear calls ``onValueChange("")``.
 */

function renderSel(
  props: Partial<React.ComponentProps<typeof ClearableSelect>>,
) {
  const onValueChange = vi.fn();
  const utils = render(
    <ClearableSelect
      value={props.value ?? ""}
      onValueChange={props.onValueChange ?? onValueChange}
      clearable={props.clearable}
      disabled={props.disabled}
    >
      <div data-testid="select-children" />
    </ClearableSelect>,
  );
  return { ...utils, onValueChange };
}

describe("ClearableSelect", () => {
  it("hides the Clear button when clearable is false (default)", () => {
    renderSel({ value: "v1" });
    expect(
      screen.queryByRole("button", { name: "Clear" }),
    ).not.toBeInTheDocument();
  });

  it("hides the Clear button when clearable=true but value is empty", () => {
    renderSel({ value: "", clearable: true });
    expect(
      screen.queryByRole("button", { name: "Clear" }),
    ).not.toBeInTheDocument();
  });

  it("hides the Clear button when disabled, even if value is truthy", () => {
    renderSel({ value: "v1", clearable: true, disabled: true });
    expect(
      screen.queryByRole("button", { name: "Clear" }),
    ).not.toBeInTheDocument();
  });

  it("shows the Clear button when clearable + truthy value + not disabled", () => {
    renderSel({ value: "v1", clearable: true });
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("calls onValueChange('') when the Clear button is clicked", async () => {
    const user = userEvent.setup();
    const { onValueChange } = renderSel({ value: "v1", clearable: true });
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(onValueChange).toHaveBeenCalledWith("");
  });

  it("forwards children to the underlying Select", () => {
    renderSel({ value: "", clearable: true });
    expect(screen.getByTestId("select-children")).toBeInTheDocument();
  });
});
