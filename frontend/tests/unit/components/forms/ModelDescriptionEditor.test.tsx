import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelDescriptionEditor } from "@/components/forms/ModelDescriptionEditor";

describe("ModelDescriptionEditor", () => {
  function setup(
    overrides: {
      open?: boolean;
      initialValue?: string | null;
      onClose?: () => void;
      onSubmit?: (v: string) => void;
    } = {},
  ) {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    render(
      <ModelDescriptionEditor
        open={overrides.open ?? true}
        initialValue={overrides.initialValue ?? null}
        onClose={overrides.onClose ?? onClose}
        onSubmit={overrides.onSubmit ?? onSubmit}
      />,
    );
    return { onClose, onSubmit };
  }

  it("shows textarea with initial value", () => {
    setup({ initialValue: "hello" });
    expect(screen.getByRole("textbox")).toHaveValue("hello");
  });

  it("calls onSubmit with updated value when Save is clicked", () => {
    const { onSubmit } = setup({ initialValue: "hello" });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "world" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onSubmit).toHaveBeenCalledWith("world");
  });

  it("calls onClose when Cancel is clicked", () => {
    const { onClose } = setup({ initialValue: "hello" });
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
