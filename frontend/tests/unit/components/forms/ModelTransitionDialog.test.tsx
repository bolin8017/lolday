import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

const { transitionMock, onCloseMock } = vi.hoisted(() => ({
  transitionMock: vi.fn(),
  onCloseMock: vi.fn(),
}));

vi.mock("@/api/queries/models", () => ({
  useTransitionModelVersion: () => ({
    mutateAsync: transitionMock,
    isPending: false,
  }),
}));

import { ModelTransitionDialog } from "@/components/forms/ModelTransitionDialog";

function renderDialog(
  overrides: Partial<{
    open: boolean;
    owner: string;
    modelName: string;
    version: number;
    currentStage: "None" | "Staging" | "Production" | "Archived";
    hasExistingProd: boolean;
  }> = {},
) {
  const props = {
    open: true,
    onClose: onCloseMock,
    owner: "alice",
    modelName: "elf-rf",
    version: 3,
    currentStage: "Staging" as const,
    hasExistingProd: false,
    ...overrides,
  };
  return render(<ModelTransitionDialog {...props} />);
}

beforeEach(() => {
  transitionMock.mockReset();
  transitionMock.mockResolvedValue(undefined);
  onCloseMock.mockReset();
});

describe("ModelTransitionDialog", () => {
  it("renders the title with version and currentStage when open", () => {
    renderDialog({ version: 7, currentStage: "Staging" });
    expect(screen.getByText(/Transition v7 from Staging/)).toBeInTheDocument();
  });

  it("does not render the dialog content when closed", () => {
    renderDialog({ open: false });
    expect(screen.queryByText(/Transition v/)).not.toBeInTheDocument();
  });

  it("shows the 'auto-archived' alert when target=Production and another version is in Production", () => {
    renderDialog({ hasExistingProd: true });
    // Default target is "Production"; alert visible because hasExistingProd.
    expect(
      screen.getByText(/Another version is currently Production/),
    ).toBeInTheDocument();
  });

  it("hides the alert when hasExistingProd is false", () => {
    renderDialog({ hasExistingProd: false });
    expect(
      screen.queryByText(/Another version is currently Production/),
    ).not.toBeInTheDocument();
  });

  it("calls the transition mutation with the expected payload on Confirm", async () => {
    const user = userEvent.setup();
    renderDialog({
      owner: "alice",
      modelName: "elf-rf",
      version: 3,
      currentStage: "Staging",
    });
    await user.click(screen.getByRole("button", { name: /Confirm/ }));
    expect(transitionMock).toHaveBeenCalledWith({
      owner: "alice",
      name: "elf-rf",
      version: 3,
      toStage: "Production",
      comment: "",
    });
    expect(onCloseMock).toHaveBeenCalled();
  });

  it("includes the typed comment in the mutation payload", async () => {
    const user = userEvent.setup();
    renderDialog();
    const textarea = screen.getByRole("textbox");
    // The Textarea is the second textbox-ish input; only one in this dialog.
    await user.type(textarea, "rollout to lab cluster");
    await user.click(screen.getByRole("button", { name: /Confirm/ }));
    expect(transitionMock).toHaveBeenCalledWith(
      expect.objectContaining({ comment: "rollout to lab cluster" }),
    );
  });

  it("calls onClose without mutating when Cancel is clicked", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: /Cancel/ }));
    expect(onCloseMock).toHaveBeenCalled();
    expect(transitionMock).not.toHaveBeenCalled();
  });

  it("Radix close (X) invokes onClose via onOpenChange(false)", () => {
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
    expect(onCloseMock).toHaveBeenCalled();
    expect(transitionMock).not.toHaveBeenCalled();
  });

  it("changes target stage via Select and submits with the chosen target", async () => {
    const user = userEvent.setup();
    renderDialog();
    // The Select trigger is labelled "Target stage"; open it and pick Archived.
    await user.click(screen.getByRole("combobox", { name: /target stage/i }));
    await user.click(screen.getByRole("option", { name: /Archived/ }));
    await user.click(screen.getByRole("button", { name: /Confirm/ }));
    expect(transitionMock).toHaveBeenCalledWith(
      expect.objectContaining({ toStage: "Archived" }),
    );
  });

  it("resets the comment to empty when the dialog re-opens", () => {
    const { rerender } = renderDialog({ open: true });
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "stale comment" } });
    expect(textarea.value).toBe("stale comment");
    // Close and re-open — the useEffect on `open` flips comment back to "".
    rerender(
      <ModelTransitionDialog
        open={false}
        onClose={onCloseMock}
        owner="alice"
        modelName="elf-rf"
        version={3}
        currentStage="Staging"
        hasExistingProd={false}
      />,
    );
    rerender(
      <ModelTransitionDialog
        open={true}
        onClose={onCloseMock}
        owner="alice"
        modelName="elf-rf"
        version={3}
        currentStage="Staging"
        hasExistingProd={false}
      />,
    );
    const fresh = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(fresh.value).toBe("");
  });
});
