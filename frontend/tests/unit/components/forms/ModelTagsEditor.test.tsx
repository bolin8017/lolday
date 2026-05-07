import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as useToastModule from "@/hooks/use-toast";
import { ModelTagsEditor } from "@/components/forms/ModelTagsEditor";

describe("ModelTagsEditor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function setup(
    overrides: {
      open?: boolean;
      initialValue?: Record<string, string>;
      onClose?: () => void;
      onSubmit?: (v: Record<string, string>) => void;
    } = {},
  ) {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    render(
      <ModelTagsEditor
        open={overrides.open ?? true}
        initialValue={overrides.initialValue ?? {}}
        onClose={overrides.onClose ?? onClose}
        onSubmit={overrides.onSubmit ?? onSubmit}
      />,
    );
    return { onClose, onSubmit };
  }

  it("calls onSubmit with parsed flat JSON", () => {
    const { onSubmit } = setup({ initialValue: {} });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: '{"x": "y"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(onSubmit).toHaveBeenCalledWith({ x: "y" });
  });

  it("shows toast error and does not call onSubmit for nested JSON", () => {
    const toastSpy = vi.spyOn(useToastModule, "toast");
    const { onSubmit } = setup({ initialValue: {} });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: '{"x": {"y": "z"}}' },
    });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(toastSpy).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("shows toast error and does not call onSubmit for invalid JSON", () => {
    const toastSpy = vi.spyOn(useToastModule, "toast");
    const { onSubmit } = setup({ initialValue: {} });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "not json" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(toastSpy).toHaveBeenCalledOnce();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
