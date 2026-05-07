import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ClearableSelect } from "@/components/forms/ClearableSelect";
import {
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

function renderHelper(value: string, onChange = vi.fn(), clearable = true) {
  return render(
    <ClearableSelect
      value={value}
      onValueChange={onChange}
      clearable={clearable}
    >
      <SelectTrigger>
        <SelectValue placeholder="Pick" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="a">A</SelectItem>
        <SelectItem value="b">B</SelectItem>
      </SelectContent>
    </ClearableSelect>,
  );
}

describe("ClearableSelect", () => {
  it("does not show clear button when value is empty", () => {
    renderHelper("");
    expect(screen.queryByRole("button", { name: /clear/i })).toBeNull();
  });

  it("shows clear button when value set and clearable=true", () => {
    renderHelper("a");
    expect(screen.getByRole("button", { name: /clear/i })).toBeInTheDocument();
  });

  it("calls onValueChange with empty string when clear clicked", async () => {
    const onChange = vi.fn();
    renderHelper("a", onChange, true);
    await userEvent.click(screen.getByRole("button", { name: /clear/i }));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("does not show clear when clearable=false even if value set", () => {
    renderHelper("a", vi.fn(), false);
    expect(screen.queryByRole("button", { name: /clear/i })).toBeNull();
  });
});
