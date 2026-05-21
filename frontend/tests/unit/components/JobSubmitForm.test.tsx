import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import { requiredFieldsForType } from "@/components/forms/JobSubmitForm.logic";
import { JobSubmitForm } from "@/components/forms/JobSubmitForm";
import { useDetectorVersions } from "@/api/queries/detectors";

// ─── mocks for JobSubmitForm rendering tests ──────────────────────────────────
type AuthRole = "admin" | "developer" | "user";
type AuthState = {
  currentUser: { email: string; role: AuthRole } | null;
  isLoading: boolean;
  isUnauthenticated: boolean;
  logout: () => void;
};
const {
  submitMutate,
  navigateMock,
  authState,
  useJobMock,
  useModelVersionMock,
} = vi.hoisted(() => ({
  submitMutate: vi.fn().mockResolvedValue({ id: "new-job-id" }),
  navigateMock: vi.fn(),
  authState: {
    current: {
      currentUser: { email: "admin@test", role: "admin" },
      isLoading: false,
      isUnauthenticated: false,
      logout: vi.fn(),
    } as AuthState,
  },
  useJobMock: vi.fn(
    (..._args: unknown[]) => ({ data: null }) as { data: unknown },
  ),
  useModelVersionMock: vi.fn(
    (..._args: unknown[]) => ({ data: null }) as { data: unknown },
  ),
}));

vi.mock("react-router", async () => {
  const actual =
    await vi.importActual<typeof import("react-router")>("react-router");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => authState.current,
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
    useJob: (...args: unknown[]) => useJobMock(...args),
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
  useModelVersion: (...args: unknown[]) => useModelVersionMock(...args),
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

// InferenceSubForm stub mirrors the TrainSubForm pattern: a "fill required
// inference" button that calls the four setter props so non-train submit
// paths are reachable. The stub also captures the props so tests can assert
// on prefill behaviour (source-model fields populated by Effect 2).
const inferenceCapture = { current: null as Record<string, unknown> | null };
vi.mock("@/components/forms/InferenceSubForm", () => ({
  InferenceSubForm: (props: {
    setSourceModelOwner: (v: string) => void;
    setSourceModelName: (v: string) => void;
    setSourceModelVersionId: (v: string) => void;
    setDerivedDetectorId: (v: string) => void;
    setDerivedDetectorVersionTag: (v: string) => void;
    setPredictDatasetId: (v: string) => void;
    setTestDatasetId: (v: string) => void;
    [k: string]: unknown;
  }) => {
    inferenceCapture.current = props;
    return (
      <button
        data-testid="fill-inference-required"
        onClick={() => {
          props.setSourceModelOwner("alice");
          props.setSourceModelName("elf-rf");
          props.setSourceModelVersionId("mv-1");
          props.setDerivedDetectorId("det-1");
          props.setDerivedDetectorVersionTag("v1.0.0");
          props.setPredictDatasetId("ds-pred");
        }}
      >
        fill inference required
      </button>
    );
  },
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
  it("returns [] for an unknown job type (default branch — defensive)", () => {
    // The function's default branch (JobSubmitForm.logic.ts:11-12) returns
    // [] for any non-{train,evaluate,predict} value. Pin so an exhaustive
    // refactor (e.g. switching to a Record<JobType, string[]> lookup table
    // without a fallback) flips this red instead of silently 500'ing on a
    // newly-added JobType variant that the form layer hasn't been updated
    // for. Cast through unknown so TS doesn't enforce the union.
    expect(
      requiredFieldsForType(
        "unknown" as unknown as Parameters<typeof requiredFieldsForType>[0],
      ),
    ).toEqual([]);
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

describe("JobSubmitForm — silent submit failure on stale tag", () => {
  // Guards the `if (!versionId)` branch in JobSubmitForm.submit(): if the
  // selected git_tag is not present in the active versionsForSubmit list
  // (e.g. an admin retired the version between page-load and submit), the
  // form must surface the localised "no longer active" message and must
  // NOT POST to /jobs. Partially closes the TODO at JobSubmitForm.tsx:133;
  // the full file-based react-router 7 integration variant is deferred
  // until a reusable createMemoryRouter harness exists — Phase 4 shipped
  // (#200, 2026-05-16) but scoped to scripts / mutation / telemetry, not
  // the router harness. Same situation as the sister docstring on
  // `tests/integration/forms/JobSubmitForm.flow.test.tsx`.
  it("shows error and skips submit when versionTag is not in versionsForSubmit", async () => {
    submitMutate.mockClear();
    // Override useDetectorVersions so the active list contains a tag that
    // does NOT match the one the TrainSubForm stub sets (`v1.0.0`).
    // Use mockReturnValue (not Once) — the form re-calls the hook on every
    // render, so the override must persist for the whole test.
    const stalePayload = {
      data: {
        items: [{ id: "ver-9", git_tag: "v9.9.9", status: "active" }],
      },
    } as unknown as ReturnType<typeof useDetectorVersions>;
    const defaultPayload = vi
      .mocked(useDetectorVersions)
      .getMockImplementation();
    vi.mocked(useDetectorVersions).mockReturnValue(stalePayload);

    renderForm();

    // TrainSubForm stub sets versionTag="v1.0.0"; versionsForSubmit
    // contains only "v9.9.9", so submit() finds no matching versionId.
    await userEvent.click(screen.getByTestId("fill-required"));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /submit job/i }),
      ).not.toBeDisabled();
    });

    await userEvent.click(screen.getByRole("button", { name: /submit job/i }));

    // Train-stage error string is the localised one from
    // JobSubmitForm.tsx:135 — "Selected detector version is no longer active."
    await waitFor(() => {
      expect(
        screen.getByText(/selected detector version is no longer active/i),
      ).toBeInTheDocument();
    });

    // submit() must short-circuit BEFORE the POST.
    expect(submitMutate).not.toHaveBeenCalled();

    // Restore the file-level default mock so later tests aren't poisoned.
    if (defaultPayload) {
      vi.mocked(useDetectorVersions).mockImplementation(defaultPayload);
    }
  });
});

describe("JobSubmitForm — race-condition fallback (§10 #22)", () => {
  // Guards the catch block at JobSubmitForm.tsx:166-168. Sibling to the
  // §10 #22 InferenceSubForm dropdown-disable test in iter 4 (PR #319):
  // if a DV gets retired between page-load (when versionsForSubmit
  // already contained the tag) and submit, the frontend filter passes
  // but the backend rejects with 422 + detail = "detector_version_id <X>
  // is no longer active". The submitError state must surface the
  // backend's detail string verbatim so the user sees actionable text
  // rather than a generic "Submit failed" sink.
  it("surfaces the backend 422 detail when submit races a DV retirement", async () => {
    // Throw with a Pydantic-shaped detail object (matches how openapi-
    // fetch surfaces error bodies on `error.detail`).
    submitMutate.mockClear();
    submitMutate.mockRejectedValueOnce({
      detail: "detector_version_id ver-1 is no longer active",
    });

    renderForm();

    // TrainSubForm stub sets versionTag=v1.0.0; the file-level mock for
    // useDetectorVersions returns it as active, so submit() reaches the
    // mutateAsync call. The mock then rejects.
    await userEvent.click(screen.getByTestId("fill-required"));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /submit job/i }),
      ).not.toBeDisabled();
    });

    await userEvent.click(screen.getByRole("button", { name: /submit job/i }));

    // The catch block surfaces e.detail verbatim. The user sees the
    // backend message, not the generic fallback.
    await waitFor(() => {
      expect(
        screen.getByText(/detector_version_id ver-1 is no longer active/i),
      ).toBeInTheDocument();
    });
    expect(submitMutate).toHaveBeenCalledTimes(1);
  });

  it("falls back to 'Submit failed' when the rejection lacks a detail field", async () => {
    // Network error / non-422 path — `e.detail` is undefined, so the
    // generic fallback string fires. Guards against a refactor that
    // silently drops the `??` short-circuit at JobSubmitForm.tsx:167.
    submitMutate.mockClear();
    submitMutate.mockRejectedValueOnce(new Error("network down"));

    renderForm();
    await userEvent.click(screen.getByTestId("fill-required"));
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /submit job/i }),
      ).not.toBeDisabled();
    });
    await userEvent.click(screen.getByRole("button", { name: /submit job/i }));

    await waitFor(() => {
      expect(screen.getByText(/submit failed/i)).toBeInTheDocument();
    });
  });
});

describe("JobSubmitForm — Cancel + role + type-switch + prefill paths", () => {
  it("Cancel button click navigates back one step via nav(-1)", async () => {
    navigateMock.mockClear();
    renderForm();
    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(navigateMock).toHaveBeenCalledWith(-1);
  });

  it("non-admin role hides the PriorityToggle card and omits priority from the submit body", async () => {
    authState.current = {
      currentUser: { email: "user@test", role: "user" },
      isLoading: false,
      isUnauthenticated: false,
      logout: vi.fn(),
    } as AuthState;
    submitMutate.mockClear();
    renderForm();
    expect(
      screen.queryByRole("button", { name: /^priority$/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^normal$/i }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("fill-required"));
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /submit job/i }),
      ).not.toBeDisabled();
    });
    await userEvent.click(screen.getByRole("button", { name: /submit job/i }));
    await waitFor(() => {
      const call = submitMutate.mock.calls[0]?.[0] as Record<string, unknown>;
      expect(call.priority).toBeUndefined();
    });
    // Restore admin for subsequent tests in this describe block.
    authState.current = {
      currentUser: { email: "admin@test", role: "admin" },
      isLoading: false,
      isUnauthenticated: false,
      logout: vi.fn(),
    } as AuthState;
  });

  it("switching job type to 'predict' renders InferenceSubForm and submits with type=predict + predict_dataset_id", async () => {
    submitMutate.mockClear();
    renderForm();
    // Switch from train → predict — the route uses Title-cased button labels
    // (Predict). The InferenceSubForm stub takes over rendering.
    await userEvent.click(screen.getByRole("button", { name: /^predict$/i }));
    expect(screen.getByTestId("fill-inference-required")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("fill-inference-required"));
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /submit job/i }),
      ).not.toBeDisabled();
    });
    await userEvent.click(screen.getByRole("button", { name: /submit job/i }));

    await waitFor(() => {
      expect(submitMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "predict",
          detector_version_id: "ver-1",
          source_model_version_id: "mv-1",
          predict_dataset_id: "ds-pred",
          // train fields are nulled out for non-train submits
          train_dataset_id: null,
        }),
      );
    });
  });

  it("prefill effect: when ?from=<predict-job> + useModelVersion resolves, the source-model fields are populated on the InferenceSubForm", async () => {
    inferenceCapture.current = null;
    // useJob returns a previously-submitted predict job carrying a
    // source_model_version_id so JobSubmitForm enables the useModelVersion
    // hook (lines 64-68) and Effect 2 fires.
    useJobMock.mockReturnValue({
      data: {
        type: "predict",
        train_dataset_id: null,
        test_dataset_id: null,
        predict_dataset_id: "ds-pred-prefill",
        source_model_version_id: "mv-prefill",
      },
    });
    useModelVersionMock.mockReturnValue({
      data: {
        id: "mv-prefill",
        owner: "alice",
        name: "elf-rf",
        detector_id: "det-prefill",
        detector_version_tag: "v2.0.0",
      },
    });
    renderForm();
    // Switch to predict so InferenceSubForm mounts.
    await userEvent.click(screen.getByRole("button", { name: /^predict$/i }));
    // After the prefillVersion effect runs, InferenceSubForm receives the
    // populated source-model props (lines 92-100 in JobSubmitForm).
    await waitFor(() => {
      expect(inferenceCapture.current).not.toBeNull();
      expect(inferenceCapture.current?.sourceModelOwner).toBe("alice");
      expect(inferenceCapture.current?.sourceModelName).toBe("elf-rf");
      expect(inferenceCapture.current?.sourceModelVersionId).toBe("mv-prefill");
      expect(inferenceCapture.current?.derivedDetectorId).toBe("det-prefill");
      expect(inferenceCapture.current?.derivedDetectorVersionTag).toBe(
        "v2.0.0",
      );
    });
    // Reset for following tests in the same module (vitest does not isolate
    // across describes within a file).
    useJobMock.mockReturnValue({ data: null });
    useModelVersionMock.mockReturnValue({ data: null });
  });
});
