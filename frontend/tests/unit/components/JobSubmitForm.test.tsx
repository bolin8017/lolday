import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import { requiredFieldsForType } from "@/components/forms/JobSubmitForm.logic";
import { JobSubmitForm } from "@/components/forms/JobSubmitForm";

// ─── mocks for JobSubmitForm rendering tests ──────────────────────────────────
const { submitMutate } = vi.hoisted(() => ({
  submitMutate: vi.fn().mockResolvedValue({ id: "new-job-id" }),
}));

vi.mock("react-router", async () => {
  const actual =
    await vi.importActual<typeof import("react-router")>("react-router");
  return {
    ...actual,
    useNavigate: () => vi.fn(),
  };
});

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({
    currentUser: { email: "admin@test", role: "admin" },
    isLoading: false,
    isUnauthenticated: false,
    logout: vi.fn(),
  }),
}));

vi.mock("@/api/queries/jobs", async () => {
  const mod =
    await vi.importActual<typeof import("@/api/queries/jobs")>(
      "@/api/queries/jobs",
    );
  return {
    ...mod,
    useSubmitJob: vi.fn(() => ({
      mutateAsync: submitMutate,
      isPending: false,
    })),
    useJob: vi.fn(() => ({ data: null })),
  };
});

vi.mock("@/api/queries/detectors", () => ({
  useDetectors: vi.fn(() => ({
    data: { items: [{ id: "det-1", display_name: "ELF RF" }] },
  })),
  useDetectorVersions: vi.fn(() => ({
    data: {
      items: [{ id: "ver-1", git_tag: "v1.0.0", status: "active" }],
    },
  })),
  useDetectorVersion: vi.fn(() => ({
    data: { manifest: { stages: { train: { params_schema: {} } } } },
  })),
}));

vi.mock("@/api/queries/models", () => ({
  useModelVersion: vi.fn(() => ({ data: null })),
}));

// HelpHint with popover=true renders a Radix Popover which triggers an
// infinite ref-update loop in jsdom/React-19 (compose-refs regression).
// Stub it out so JobSubmitForm rendering tests don't blow up.
vi.mock("@/components/common/HelpHint", () => ({
  HelpHint: ({ children }: { children: React.ReactNode }) => (
    <span data-testid="help-hint">{children}</span>
  ),
}));

// TrainSubForm and InferenceSubForm include multiple Radix Select components
// which, when composed together in jsdom/React-19, trigger the compose-refs
// infinite loop. Stub both; their behaviour is tested in their own test files.
//
// TrainSubForm is stubbed with a "fill required" button that calls the three
// setter props so integration tests can reach the submit path without
// interacting with real Radix Select dropdowns.
vi.mock("@/components/forms/TrainSubForm", () => ({
  TrainSubForm: (props: {
    setDetectorId: (v: string) => void;
    setVersionTag: (v: string) => void;
    setTrainDatasetId: (v: string) => void;
    [k: string]: unknown;
  }) => (
    <button
      data-testid="fill-required"
      onClick={() => {
        props.setDetectorId("det-1");
        props.setVersionTag("v1.0.0");
        props.setTrainDatasetId("ds-1");
      }}
    >
      fill required
    </button>
  ),
}));

vi.mock("@/components/forms/InferenceSubForm", () => ({
  InferenceSubForm: () => <div data-testid="inference-sub-form" />,
}));

vi.mock("@/components/forms/StageExplainer", () => ({
  StageExplainer: () => <div data-testid="stage-explainer" />,
}));

vi.mock("@/components/forms/StickyFormFooter", () => ({
  StickyFormFooter: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="sticky-footer">{children}</div>
  ),
}));

function renderForm() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <JobSubmitForm />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("requiredFieldsForType", () => {
  it("train needs only train_dataset (test is optional)", () => {
    expect(requiredFieldsForType("train")).toEqual(["train_dataset_id"]);
  });
  it("evaluate needs test+source_model", () => {
    expect(requiredFieldsForType("evaluate")).toEqual([
      "test_dataset_id",
      "source_model_version_id",
    ]);
  });
  it("predict needs predict+source_model", () => {
    expect(requiredFieldsForType("predict")).toEqual([
      "predict_dataset_id",
      "source_model_version_id",
    ]);
  });
});

describe("phase 11e — JSON textarea path removed", () => {
  it("does not export parseParams", async () => {
    const mod = await import("@/components/forms/JobSubmitForm.logic");
    expect(mod).not.toHaveProperty("parseParams");
  });

  it("does not export ParseParamsResult type as runtime value", async () => {
    const mod = await import("@/components/forms/JobSubmitForm.logic");
    expect(mod).not.toHaveProperty("ParseParamsResult");
  });
});

describe("JobSubmitForm — PriorityToggle (admin)", () => {
  it("renders Normal button pressed by default", () => {
    renderForm();
    expect(screen.getByRole("button", { name: /normal/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("toggling Priority sets aria-pressed=true on the Priority button", async () => {
    renderForm();
    await userEvent.click(screen.getByRole("button", { name: /^priority$/i }));
    expect(screen.getByRole("button", { name: /^priority$/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /normal/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });
});

describe("JobSubmitForm — priority integration (submit payload)", () => {
  it("admin: toggling Priority active causes the submit body to carry priority: 1", async () => {
    submitMutate.mockClear();
    renderForm();

    // Toggle priority to active
    await userEvent.click(screen.getByRole("button", { name: /^priority$/i }));
    expect(screen.getByRole("button", { name: /^priority$/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    // Fill required fields via the TrainSubForm setter button
    await userEvent.click(screen.getByTestId("fill-required"));

    // Wait for canSubmit to flip — Submit button becomes enabled
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /submit job/i }),
      ).not.toBeDisabled();
    });

    // Submit
    await userEvent.click(screen.getByRole("button", { name: /submit job/i }));

    // The submit body must carry priority: 1, the resolved versionId, and the
    // train dataset id set by the mock setter
    await waitFor(() => {
      expect(submitMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          priority: 1,
          type: "train",
          detector_version_id: "ver-1",
          train_dataset_id: "ds-1",
        }),
      );
    });
  });

  it("admin: keeping Normal (priority=0) omits the priority field from the submit body", async () => {
    submitMutate.mockClear();
    renderForm();

    // Do not toggle priority — stays at 0 (Normal)
    await userEvent.click(screen.getByTestId("fill-required"));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /submit job/i }),
      ).not.toBeDisabled();
    });

    await userEvent.click(screen.getByRole("button", { name: /submit job/i }));

    // The submit body must NOT contain a priority field (production spread:
    // `...(isAdmin && priority !== 0 ? { priority } : {})`)
    await waitFor(() => {
      expect(submitMutate).toHaveBeenCalledTimes(1);
      const call = submitMutate.mock.calls[0][0] as Record<string, unknown>;
      expect(call.priority).toBeUndefined();
    });
  });
});
