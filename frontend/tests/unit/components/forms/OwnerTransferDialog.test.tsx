import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OwnerTransferDialog } from "@/components/forms/OwnerTransferDialog";

describe("OwnerTransferDialog", () => {
  function setup(
    overrides: {
      open?: boolean;
      onClose?: () => void;
      onSubmit?: (newOwner: string, comment: string | null) => void;
    } = {},
  ) {
    const onClose = overrides.onClose ?? vi.fn();
    const onSubmit = overrides.onSubmit ?? vi.fn();
    const utils = render(
      <OwnerTransferDialog
        open={overrides.open ?? true}
        onClose={onClose}
        onSubmit={onSubmit}
      />,
    );
    return { onClose, onSubmit, ...utils };
  }

  it("disables submit button when handle is empty", () => {
    setup();
    expect(screen.getByRole("button", { name: /transfer/i })).toBeDisabled();
  });

  it("enables submit button when handle is non-empty", () => {
    setup();
    fireEvent.change(screen.getByRole("textbox", { name: /new owner/i }), {
      target: { value: "bob" },
    });
    expect(screen.getByRole("button", { name: /transfer/i })).toBeEnabled();
  });

  it("calls onSubmit with handle and null comment when no comment entered", () => {
    const { onSubmit } = setup();
    fireEvent.change(screen.getByRole("textbox", { name: /new owner/i }), {
      target: { value: "bob" },
    });
    fireEvent.click(screen.getByRole("button", { name: /transfer/i }));
    expect(onSubmit).toHaveBeenCalledWith("bob", null);
  });

  it("forwards the comment textarea when present", () => {
    const { onSubmit } = setup();
    fireEvent.change(screen.getByRole("textbox", { name: /new owner/i }), {
      target: { value: "bob" },
    });
    fireEvent.change(screen.getByPlaceholderText(/optional comment/i), {
      target: { value: "promoted" },
    });
    fireEvent.click(screen.getByRole("button", { name: /transfer/i }));
    expect(onSubmit).toHaveBeenCalledWith("bob", "promoted");
  });

  it("Cancel button invokes onClose", () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Radix close (X) invokes onClose via the onOpenChange(false) branch", () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("re-opening the dialog clears prior handle/comment state", () => {
    // Open + type, then close (open=false) + reopen (open=true).
    // The useEffect resets handle/comment whenever `open` flips true.
    const { rerender } = setup({ open: true });
    fireEvent.change(screen.getByRole("textbox", { name: /new owner/i }), {
      target: { value: "stale" },
    });
    rerender(
      <OwnerTransferDialog
        open={false}
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    rerender(
      <OwnerTransferDialog
        open={true}
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    expect(screen.getByRole("textbox", { name: /new owner/i })).toHaveValue("");
    expect(screen.getByRole("button", { name: /transfer/i })).toBeDisabled();
  });
});
