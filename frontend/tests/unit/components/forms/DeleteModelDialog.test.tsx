import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DeleteModelDialog } from "@/components/forms/DeleteModelDialog";

describe("DeleteModelDialog", () => {
  function setup(
    overrides: {
      open?: boolean;
      owner?: string;
      name?: string;
      onClose?: () => void;
      onConfirm?: () => void;
    } = {},
  ) {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(
      <DeleteModelDialog
        open={overrides.open ?? true}
        owner={overrides.owner ?? "alice"}
        name={overrides.name ?? "my-model"}
        onClose={overrides.onClose ?? onClose}
        onConfirm={overrides.onConfirm ?? onConfirm}
      />,
    );
    return { onClose, onConfirm };
  }

  it("disables Delete button when input is empty", () => {
    setup();
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
  });

  it("disables Delete button when input does not match fullName", () => {
    setup();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "alice/wrong-name" },
    });
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
  });

  it("enables Delete button when input matches owner/name", () => {
    setup();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "alice/my-model" },
    });
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeEnabled();
  });

  it("calls onConfirm when Delete is clicked with matching text", () => {
    const { onConfirm } = setup();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "alice/my-model" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});
