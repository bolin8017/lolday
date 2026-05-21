import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("Cancel button click fires onClose (footer escape hatch)", () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Dialog.onOpenChange(false) fires onClose via the L40 `(o) => !o && onClose()` branch (Escape key)", async () => {
    // Radix Dialog's onOpenChange callback flips when the user dismisses via
    // Escape, an outside click, or the X button. Source L40 maps the false
    // arm to onClose(); the true arm is a no-op (dialog already open).
    const user = userEvent.setup();
    const { onClose } = setup();
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(onClose).toHaveBeenCalledOnce();
    });
  });

  it("input re-binds to empty when reopening: useEffect resets `confirm` on open transition (L33-35)", () => {
    // The useEffect at L33-35 resets the local `confirm` state to "" every
    // time `open` flips to true. Without it, an aborted-then-reopened delete
    // would carry the prior typed text, leaving the Delete button immediately
    // enabled with stale input. Tests both the open→open→open cycle and the
    // empty-state landing.
    const { rerender } = render(
      <DeleteModelDialog
        open={false}
        owner="alice"
        name="my-model"
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    rerender(
      <DeleteModelDialog
        open={true}
        owner="alice"
        name="my-model"
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    const textbox = screen.getByRole("textbox") as HTMLInputElement;
    expect(textbox.value).toBe("");
    fireEvent.change(textbox, { target: { value: "alice/my-model" } });
    expect(textbox.value).toBe("alice/my-model");
    // Close + reopen — the useEffect must reset back to "".
    rerender(
      <DeleteModelDialog
        open={false}
        owner="alice"
        name="my-model"
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    rerender(
      <DeleteModelDialog
        open={true}
        owner="alice"
        name="my-model"
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    const reopenedTextbox = screen.getByRole("textbox") as HTMLInputElement;
    expect(reopenedTextbox.value).toBe("");
  });
});
