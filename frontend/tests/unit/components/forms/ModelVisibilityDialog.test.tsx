import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelVisibilityDialog } from "@/components/forms/ModelVisibilityDialog";

describe("ModelVisibilityDialog", () => {
  function setup(
    overrides: {
      open?: boolean;
      current?: "public" | "private";
      onClose?: () => void;
      onSubmit?: (v: "public" | "private", comment: string | null) => void;
    } = {},
  ) {
    const onClose = vi.fn();
    const onSubmit = vi.fn();
    render(
      <ModelVisibilityDialog
        open={overrides.open ?? true}
        current={overrides.current ?? "public"}
        onClose={overrides.onClose ?? onClose}
        onSubmit={overrides.onSubmit ?? onSubmit}
      />,
    );
    return { onClose, onSubmit };
  }

  it("calls onSubmit with ('private', null) when current is public and no comment", () => {
    const { onSubmit } = setup({ current: "public" });
    // Find and click the confirm button (title key: makePrivate)
    const buttons = screen.getAllByRole("button");
    const submitBtn = buttons.find(
      (b) =>
        b.textContent?.match(/private/i) && !b.textContent?.match(/cancel/i),
    );
    if (!submitBtn) throw new Error("Submit button not found");
    fireEvent.click(submitBtn);
    expect(onSubmit).toHaveBeenCalledWith("private", null);
  });

  it("calls onSubmit with ('public', null) when current is private and no comment", () => {
    const { onSubmit } = setup({ current: "private" });
    const buttons = screen.getAllByRole("button");
    const submitBtn = buttons.find(
      (b) =>
        b.textContent?.match(/public/i) && !b.textContent?.match(/cancel/i),
    );
    if (!submitBtn) throw new Error("Submit button not found");
    fireEvent.click(submitBtn);
    expect(onSubmit).toHaveBeenCalledWith("public", null);
  });

  it("calls onClose when Cancel is clicked", () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
