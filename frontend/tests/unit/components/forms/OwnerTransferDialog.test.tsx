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
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    render(
      <OwnerTransferDialog
        open={overrides.open ?? true}
        onClose={overrides.onClose ?? onClose}
        onSubmit={overrides.onSubmit ?? onSubmit}
      />,
    );
    return { onClose, onSubmit };
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
});
