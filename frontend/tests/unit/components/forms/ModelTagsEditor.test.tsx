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

  it("Cancel button invokes onClose", () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Radix close (X) invokes onClose via onOpenChange(false)", () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("useEffect re-syncs JSON when dialog re-opens with a new initialValue", () => {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    const { rerender } = render(
      <ModelTagsEditor
        open={true}
        initialValue={{ env: "prod" }}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByRole("textbox")).toHaveValue(
      JSON.stringify({ env: "prod" }, null, 2),
    );
    rerender(
      <ModelTagsEditor
        open={true}
        initialValue={{ env: "staging", region: "tw" }}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByRole("textbox")).toHaveValue(
      JSON.stringify({ env: "staging", region: "tw" }, null, 2),
    );
  });

  it("useEffect does NOT re-sync when dialog is closed (covers `if (open)` false branch)", () => {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    const { rerender } = render(
      <ModelTagsEditor
        open={true}
        initialValue={{ env: "prod" }}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    // Edit then close; re-open with the now-different initialValue. The
    // useEffect's open=false render must not have run setValue, so re-opening
    // is what finally syncs to the new value — proving the closed-branch
    // skipped the sync.
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: '{"user": "typed"}' },
    });
    rerender(
      <ModelTagsEditor
        open={false}
        initialValue={{ env: "staging" }}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    rerender(
      <ModelTagsEditor
        open={true}
        initialValue={{ env: "staging" }}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByRole("textbox")).toHaveValue(
      JSON.stringify({ env: "staging" }, null, 2),
    );
  });
});
