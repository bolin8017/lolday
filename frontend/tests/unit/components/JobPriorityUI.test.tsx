/**
 * Phase 6 (Task G.6) — vitest/RTL tests for admin-only priority controls.
 *
 * Covers:
 * - JobDetailShell: admin sees Priority row; non-admin does not
 * - JobDetailShell: PriorityEditor shows edit input for queued_backend; read-only for other statuses
 * - JobsListPage: admin sees Priority column; non-admin does not
 * - usePatchJob is called on save in PriorityEditor
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { JobDetailShell } from "@/components/jobs/JobDetailShell";
import type { components } from "@/api/schema.gen";

// ─── hoisted mock state ────────────────────────────────────────────────────
type Role = "user" | "developer" | "admin";

const { authState, patchMutate } = vi.hoisted(() => ({
  authState: { role: "admin" as Role },
  patchMutate: vi.fn(),
}));

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({
    currentUser: { email: "lab@test", role: authState.role },
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
    useCancelJob: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
    usePatchJob: vi.fn(() => ({
      mutate: patchMutate,
      isPending: false,
    })),
    useJobs: vi.fn(() => ({ data: { items: [] }, isLoading: false })),
  };
});

vi.mock("@/api/queries/cluster", () => ({
  useJobQueuePosition: vi.fn(() => ({ data: null })),
}));

// ─── helpers ───────────────────────────────────────────────────────────────
type JobRead = components["schemas"]["JobRead"];

const makeJob = (overrides: Partial<JobRead> = {}): JobRead =>
  ({
    id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    type: "train",
    status: "queued_backend",
    detector_version_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    owner_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    mlflow_run_id: null,
    mlflow_experiment_id: null,
    k8s_job_name: null,
    failure_reason: null,
    submitted_at: "2026-05-05T00:00:00Z",
    started_at: null,
    finished_at: null,
    train_dataset_id: null,
    test_dataset_id: null,
    predict_dataset_id: null,
    source_model_version_id: null,
    resolved_config: {},
    log_tail: null,
    resource_profile: "tiny",
    priority: 0,
    ...overrides,
  }) as unknown as JobRead;

function renderShell(job: JobRead) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <JobDetailShell job={job}>
          <div data-testid="children" />
        </JobDetailShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ─── tests ─────────────────────────────────────────────────────────────────
describe("JobDetailShell — priority section", () => {
  beforeEach(() => {
    authState.role = "admin";
    vi.clearAllMocks();
  });

  it("admin sees Priority row in metadata", () => {
    renderShell(makeJob());
    // At least one element matching "Priority" exists in the metadata card
    expect(screen.getAllByText(/priority/i).length).toBeGreaterThan(0);
  });

  it("non-admin does not see Priority row", () => {
    authState.role = "user";
    renderShell(makeJob({ status: "succeeded" }));
    expect(screen.queryAllByText(/priority/i)).toHaveLength(0);
  });

  it("shows editable input for queued_backend status", () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    expect(
      screen.getByRole("spinbutton", { name: /priority/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /save priority/i }),
    ).toBeInTheDocument();
  });

  it("shows read-only value for non-queued_backend status", () => {
    renderShell(makeJob({ status: "running", priority: 5 }));
    // no spin button — just a static span
    expect(screen.queryByRole("spinbutton")).toBeNull();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("save button is disabled when draft equals current priority", () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    const saveBtn = screen.getByRole("button", { name: /save priority/i });
    expect(saveBtn).toBeDisabled();
  });

  it("save button is enabled after changing the draft value", async () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    const input = screen.getByRole("spinbutton", { name: /priority/i });
    fireEvent.change(input, { target: { value: "3" } });
    const saveBtn = screen.getByRole("button", { name: /save priority/i });
    expect(saveBtn).not.toBeDisabled();
  });

  it("calls usePatchJob mutate on save", async () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    const input = screen.getByRole("spinbutton", { name: /priority/i });
    fireEvent.change(input, { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /save priority/i }));
    await waitFor(() => {
      expect(patchMutate).toHaveBeenCalledWith(
        {
          id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          priority: 2,
        },
        expect.any(Object),
      );
    });
  });

  it("shows UX warning when draft > 0", () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    const input = screen.getByRole("spinbutton", { name: /priority/i });
    fireEvent.change(input, { target: { value: "1" } });
    // Warning text (partial match to be locale-agnostic in unit tests)
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("does not show UX warning when draft is 0", () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

// ─── jobs list priority column visibility ──────────────────────────────────
import JobsListPage from "@/routes/_authed.jobs._index";

function renderListPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/jobs"]}>
        <Routes>
          <Route path="/jobs" element={<JobsListPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("JobsListPage — priority column visibility", () => {
  beforeEach(() => {
    authState.role = "admin";
    vi.clearAllMocks();
  });

  it("admin sees Priority column header", () => {
    renderListPage();
    expect(screen.getByText(/priority/i)).toBeInTheDocument();
  });

  it("non-admin does not see Priority column header", () => {
    authState.role = "user";
    renderListPage();
    expect(screen.queryByText(/priority/i)).toBeNull();
  });
});
