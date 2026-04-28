import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { DeleteConfirmDialog } from "@/components/common/DeleteConfirmDialog";

describe("DeleteConfirmDialog", () => {
  function setup(overrides: Partial<React.ComponentProps<typeof DeleteConfirmDialog>> = {}) {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const onOpenChange = vi.fn();
    const props: React.ComponentProps<typeof DeleteConfirmDialog> = {
      open: true,
      onOpenChange,
      title: "Delete detector elfrfdet?",
      description: "This will purge Harbor images.",
      confirmText: "elfrfdet",
      onConfirm,
      pending: false,
      errorBanner: null,
      ...overrides,
    };
    render(<DeleteConfirmDialog {...props} />);
    return { onConfirm, onOpenChange };
  }

  it("renders title and description", () => {
    setup();
    expect(screen.getByText(/Delete detector elfrfdet/)).toBeInTheDocument();
    expect(screen.getByText(/purge Harbor images/)).toBeInTheDocument();
  });

  it("Delete button is disabled when input is empty", () => {
    setup();
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
  });

  it("Delete button is disabled with wrong text", () => {
    setup();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Elfrfdet" } });
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
  });

  it("Delete button is enabled with exact match", () => {
    setup();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "elfrfdet" } });
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeEnabled();
  });

  it("calls onConfirm when Delete clicked with match", async () => {
    const { onConfirm } = setup();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "elfrfdet" } });
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("shows pending state on Delete button", () => {
    setup({ pending: true });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "elfrfdet" } });
    expect(screen.getByRole("button", { name: /deleting/i })).toBeDisabled();
  });

  it("shows error banner when errorBanner provided", () => {
    setup({
      errorBanner: {
        code: "version_has_in_flight_jobs",
        message: "Cancel running jobs that use this version before deleting it.",
      },
    });
    expect(screen.getByText(/Cancel running jobs/)).toBeInTheDocument();
  });

  it("does not close dialog on error", () => {
    const { onOpenChange } = setup({
      errorBanner: { code: "X", message: "Y" },
    });
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
