import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";

const { deleteMut, capturedDialogProps } = vi.hoisted(() => ({
  deleteMut: {
    mutateAsync: vi.fn(),
    isPending: false,
  },
  capturedDialogProps: {
    current: undefined as
      | {
          open: boolean;
          title: React.ReactNode;
          confirmText: string;
          pending: boolean;
          errorBanner: unknown;
          onConfirm: () => Promise<void>;
          onOpenChange: (o: boolean) => void;
        }
      | undefined,
  },
}));

vi.mock("@/api/queries/detectors", () => ({
  useDeleteDetector: () => deleteMut,
}));

// Stub DeleteConfirmDialog so the test focuses on the wire-up between
// the dropdown trigger, dialog open/close state, and the
// onConfirm → mutateAsync handoff. The dialog itself has its own
// dedicated suite (frontend/tests/unit/DeleteConfirmDialog.test.tsx).
vi.mock("@/components/common/DeleteConfirmDialog", () => ({
  DeleteConfirmDialog: (props: {
    open: boolean;
    title: React.ReactNode;
    confirmText: string;
    pending: boolean;
    errorBanner: unknown;
    onConfirm: () => Promise<void>;
    onOpenChange: (o: boolean) => void;
  }) => {
    capturedDialogProps.current = props;
    if (!props.open) return null;
    return (
      <div data-testid="stub-delete-dialog">
        <span data-testid="stub-confirm-text">{props.confirmText}</span>
        <button
          type="button"
          data-testid="stub-dialog-confirm"
          onClick={() => {
            void props.onConfirm();
          }}
        />
      </div>
    );
  },
}));

vi.mock("@/api/errors", () => {
  // Mirror the real (status, detail, fieldErrors, structuredDetail) shape
  // so TS-checked call sites stay honest. Only structuredDetail is read
  // by DetectorRowActions; the other fields exist to match the
  // constructor arity.
  class LoldayApiError extends Error {
    status: number;
    detail: string;
    structuredDetail?: unknown;
    constructor(
      status: number,
      detail: string,
      _fieldErrors: unknown[] = [],
      structuredDetail?: unknown,
    ) {
      super(detail);
      this.status = status;
      this.detail = detail;
      this.structuredDetail = structuredDetail;
    }
  }
  return { LoldayApiError };
});

vi.mock("@/components/common/deleteErrorBanner", () => ({
  detailToDeleteBanner: (detail: unknown) =>
    detail ? { kind: "stubbed-banner", detail } : null,
}));

import { DetectorRowActions } from "@/routes/_authed.detectors._index";
import { LoldayApiError } from "@/api/errors";

const detector = {
  id: "det-1",
  name: "elfrf",
  display_name: "ELF RF",
};

beforeEach(() => {
  deleteMut.mutateAsync.mockReset();
  deleteMut.isPending = false;
  capturedDialogProps.current = undefined;
});

describe("_authed.detectors._index.tsx (DetectorRowActions)", () => {
  it("renders the row trigger with the per-detector aria-label", () => {
    const { getByRole } = render(<DetectorRowActions detector={detector} />);
    // axe button-name (WCAG 2.1 AA): icon-only trigger must carry a name.
    // The label embeds display_name so screen readers can disambiguate
    // between rows on the detector list page.
    const trigger = getByRole("button", {
      name: /Row actions for detector ELF RF/i,
    });
    expect(trigger).toBeInTheDocument();
  });

  it("does NOT render the delete dialog before the user opens it", () => {
    const { queryByTestId } = render(
      <DetectorRowActions detector={detector} />,
    );
    expect(queryByTestId("stub-delete-dialog")).toBeNull();
    expect(capturedDialogProps.current?.open).toBe(false);
  });
});

describe("_authed.detectors._index.tsx (DetectorRowActions) — full flow", () => {
  async function openDialog() {
    const utils = render(<DetectorRowActions detector={detector} />);
    // Radix DropdownMenu sets pointer-events: none on the document body
    // while open to block underlying interactions. user-event v14's
    // strict pointerEventsCheck reads that as "click target unclickable"
    // — disable the check (we explicitly want to drive the dialog under
    // its open-state).
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    await user.click(
      utils.getByRole("button", { name: /Row actions for detector/ }),
    );
    // The dropdown menu item carries the literal text 'Delete detector'.
    // findByText walks the portal so we don't need queryAllBy on the body.
    const item = await utils.findByText(/Delete detector/);
    await user.click(item);
    return { utils, user };
  }

  it("clicking the 'Delete detector' menu item opens the dialog with slug confirmText", async () => {
    const { utils } = await openDialog();
    // Slug name not display_name (Phase 13a fix per the source comment):
    // typing 'elfrf' is feasible; 'ELF RF' has space + mixed case.
    expect(utils.getByTestId("stub-confirm-text")).toHaveTextContent("elfrf");
  });

  it("confirming the dialog calls deleteMut.mutateAsync(detector.id)", async () => {
    deleteMut.mutateAsync.mockResolvedValueOnce(undefined);
    const { utils, user } = await openDialog();
    await user.click(utils.getByTestId("stub-dialog-confirm"));
    expect(deleteMut.mutateAsync).toHaveBeenCalledTimes(1);
    expect(deleteMut.mutateAsync).toHaveBeenCalledWith("det-1");
  });

  it("successful confirm closes the dialog (onOpenChange(false))", async () => {
    deleteMut.mutateAsync.mockResolvedValueOnce(undefined);
    const { utils, user } = await openDialog();
    expect(utils.queryByTestId("stub-delete-dialog")).toBeInTheDocument();
    await user.click(utils.getByTestId("stub-dialog-confirm"));
    // Source: `setOpen(false)` after a successful mutateAsync — pin so a
    // refactor doesn't leave the dialog open after a delete succeeds.
    expect(utils.queryByTestId("stub-delete-dialog")).toBeNull();
  });

  it("LoldayApiError on confirm surfaces the structuredDetail through detailToDeleteBanner", async () => {
    // Pin the error path: when delete rejects with our typed API error,
    // the structuredDetail is shaped via detailToDeleteBanner and shown
    // as the dialog's errorBanner. The dialog stays open so the user
    // can see the explanation.
    deleteMut.mutateAsync.mockRejectedValueOnce(
      new LoldayApiError(409, "detector has runs", [], {
        code: "DETECTOR_HAS_RUNS",
      } as unknown as ConstructorParameters<typeof LoldayApiError>[3]),
    );
    const { utils, user } = await openDialog();
    await user.click(utils.getByTestId("stub-dialog-confirm"));
    // Dialog stays open + errorBanner populated via our stub.
    expect(utils.queryByTestId("stub-delete-dialog")).toBeInTheDocument();
    expect(capturedDialogProps.current?.errorBanner).toMatchObject({
      kind: "stubbed-banner",
    });
  });

  it("non-LoldayApiError rejections pass detailToDeleteBanner(undefined) (null banner)", async () => {
    // Defensive: a generic Error doesn't carry structuredDetail, so the
    // banner stays null. Pin so a future refactor doesn't crash on the
    // unknown-error path.
    deleteMut.mutateAsync.mockRejectedValueOnce(new Error("network down"));
    const { utils, user } = await openDialog();
    await user.click(utils.getByTestId("stub-dialog-confirm"));
    expect(utils.queryByTestId("stub-delete-dialog")).toBeInTheDocument();
    expect(capturedDialogProps.current?.errorBanner).toBeNull();
  });

  it("closing the dialog (onOpenChange(false)) resets the error banner", async () => {
    deleteMut.mutateAsync.mockRejectedValueOnce(
      new LoldayApiError(409, "conflict", [], {
        code: "X",
      } as unknown as ConstructorParameters<typeof LoldayApiError>[3]),
    );
    const { utils, user } = await openDialog();
    await user.click(utils.getByTestId("stub-dialog-confirm"));
    // Banner is non-null after the error
    expect(capturedDialogProps.current?.errorBanner).not.toBeNull();
    // Close the dialog via the captured onOpenChange callback (the way
    // the real dialog's overlay-click / escape-key path triggers it).
    capturedDialogProps.current?.onOpenChange(false);
    // After close, re-opening must NOT carry stale error state.
    // Re-render via reopen flow: capturedDialogProps remains updated by
    // the parent's state-setter, but our stub only renders when open=true.
    // Open again and assert errorBanner is back to null.
    await user.click(
      utils.getByRole("button", { name: /Row actions for detector/ }),
    );
    const item = await utils.findByText(/Delete detector/);
    await user.click(item);
    expect(capturedDialogProps.current?.errorBanner).toBeNull();
  });

  it("forwards deleteMut.isPending to the dialog's `pending` prop", async () => {
    deleteMut.isPending = true;
    const { utils } = await openDialog();
    // Pin the pending-state propagation: while the mutation runs, the
    // dialog's confirm button must be disabled (the dialog itself owns
    // that UI; this test pins the wire-up).
    expect(capturedDialogProps.current?.pending).toBe(true);
    // Defensive: the dialog still renders.
    expect(utils.queryByTestId("stub-delete-dialog")).toBeInTheDocument();
  });
});
