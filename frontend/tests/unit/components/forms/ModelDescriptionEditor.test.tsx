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
    const utils = render(
      <ModelDescriptionEditor
        open={overrides.open ?? true}
        initialValue={overrides.initialValue ?? null}
        onClose={overrides.onClose ?? onClose}
        onSubmit={overrides.onSubmit ?? onSubmit}
      />,
    );
    return { onClose, onSubmit, ...utils };
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

  it("Radix close (X) invokes onClose via onOpenChange(false)", () => {
    const { onClose } = setup({ initialValue: "hello" });
    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("renders empty textarea when initialValue is null (covers `?? ''` fallback)", () => {
    setup({ initialValue: null });
    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.getByText("0 / 5000")).toBeInTheDocument();
  });

  it("useEffect re-syncs value when dialog re-opens with a new initialValue", () => {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    const { rerender } = render(
      <ModelDescriptionEditor
        open={true}
        initialValue="first"
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByRole("textbox")).toHaveValue("first");
    // Re-open with a different initial value — useEffect's `if (open)` true
    // branch should overwrite the stale state.
    rerender(
      <ModelDescriptionEditor
        open={true}
        initialValue="second"
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByRole("textbox")).toHaveValue("second");
  });

  it("useEffect does NOT re-sync value when dialog is closed (covers `if (open)` false branch)", () => {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    const { rerender } = render(
      <ModelDescriptionEditor
        open={true}
        initialValue="kept"
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    // Edit the value, then close while changing initialValue: the closed
    // dialog must retain the user's edit so re-opening doesn't blast it.
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "user-typed" },
    });
    rerender(
      <ModelDescriptionEditor
        open={false}
        initialValue="changed-while-closed"
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    // Re-open with same closed-time initialValue — useEffect should run with
    // open=true and now sync to "changed-while-closed", proving the closed
    // path did NOT overwrite the user-typed buffer.
    rerender(
      <ModelDescriptionEditor
        open={true}
        initialValue="changed-while-closed"
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByRole("textbox")).toHaveValue("changed-while-closed");
  });
});
