import React from "react";
import { render, screen } from "@testing-library/react";
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
vi.mock("@/components/forms/TrainSubForm", () => ({
  TrainSubForm: () => <div data-testid="train-sub-form" />,
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
    // Click the Priority (⚡) button in the toggle
    await userEvent.click(screen.getByRole("button", { name: /^priority$/i }));
    // Fill required train fields: select detector version tag
    // TrainSubForm renders a detector version selector; we bypass the full UI
    // by directly submitting — canSubmit gate requires detectorId + versionTag,
    // so we verify the priority toggle state via aria-pressed only, not the
    // full submit path (which requires selecting external dropdown values).
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
