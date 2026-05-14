/**
 * Phase 6 (Task G.6) — vitest/RTL tests for admin-only priority controls.
 *
 * Covers:
 * - JobDetailShell: admin sees Priority row; non-admin does not
 * - JobDetailShell: PriorityEditor shows edit input for queued_backend; read-only for other statuses
 * - JobsListPage: admin sees Priority column; non-admin does not
 * - usePatchJob is called on save in PriorityEditor
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
  type MockedFunction,
} from "vitest";
import { useJobs } from "@/api/queries/jobs";

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
    expect(screen.getAllByText(/priority/i).length).toBeGreaterThan(0);
  });

  it("non-admin does not see Priority row", () => {
    authState.role = "user";
    renderShell(makeJob({ status: "succeeded" }));
    expect(screen.queryAllByText(/priority/i)).toHaveLength(0);
  });

  it("shows toggle for queued_backend status", () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    expect(screen.getByRole("button", { name: /normal/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /priority/i }),
    ).toBeInTheDocument();
  });

  it("shows read-only badge for non-queued_backend status (priority=1)", () => {
    renderShell(makeJob({ status: "running", priority: 1 }));
    // Badge renders with the ⚡ glyph for priority=1
    expect(screen.getByText(/⚡/)).toBeInTheDocument();
    // No toggle button rendered for read-only status
    expect(screen.queryByRole("button", { name: /normal/i })).toBeNull();
  });

  it("shows read-only Normal text for non-queued_backend status (priority=0)", () => {
    renderShell(makeJob({ status: "running", priority: 0 }));
    expect(screen.queryByRole("button", { name: /normal/i })).toBeNull();
    expect(screen.getByText(/normal/i)).toBeInTheDocument();
  });

  it("auto-saves on toggle without a separate Save button", async () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /priority/i }));
    await waitFor(() => {
      expect(patchMutate).toHaveBeenCalledWith({
        id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        priority: 1,
      });
    });
  });

  it("shows warning when Priority is active", () => {
    renderShell(makeJob({ status: "queued_backend", priority: 1 }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("does not show warning when Normal is active", () => {
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
          {/* Absorb row-click navigations so the test DOM is not cleared */}
          <Route path="/jobs/:id" element={<div />} />
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

// ─── PriorityCell branch coverage — terminal/running statuses ─────────────
describe("JobsListPage — PriorityCell branch coverage", () => {
  beforeEach(() => {
    authState.role = "admin";
    vi.clearAllMocks();
  });

  // The 2026-05-08 fix added "timeout" to the dash branch. Regression-guard
  // it by asserting the dash for each terminal/running status.
  it.each(["running", "succeeded", "failed", "cancelled", "timeout"] as const)(
    "shows '—' for non-editable status %s",
    (status) => {
      (useJobs as MockedFunction<typeof useJobs>).mockReturnValueOnce({
        data: {
          items: [
            {
              id: "j1",
              type: "train",
              status,
              priority: 1,
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
              summary_metrics: null,
            },
          ],
        },
        isLoading: false,
      } as unknown as ReturnType<typeof useJobs>);
      renderListPage();
      // For terminal/running statuses, PriorityCell renders a muted-foreground
      // "—" span (not the table's generic empty-cell dashes). Use getAllByText
      // and assert at least one match has the expected class.
      const dashes = screen.getAllByText("—");
      const priorityDash = dashes.find(
        (el) =>
          el.tagName === "SPAN" &&
          el.className.includes("text-muted-foreground") &&
          el.className.includes("text-xs"),
      );
      expect(priorityDash).toBeInTheDocument();
    },
  );
});

describe("JobsListPage — priority cell popover", () => {
  beforeEach(() => {
    authState.role = "admin";
    vi.clearAllMocks();
  });

  it("clicking a priority badge for queued_backend opens a popover with the toggle", async () => {
    // Override useJobs to seed a queued_backend row so PriorityCell renders
    (useJobs as MockedFunction<typeof useJobs>).mockReturnValueOnce({
      data: {
        items: [
          {
            id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
            type: "train",
            status: "queued_backend",
            priority: 0,
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
            summary_metrics: null,
          },
        ],
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useJobs>);
    renderListPage();
    const badges = screen.getAllByRole("button", { name: /priority/i });
    await userEvent.click(badges[0]);
    expect(
      await screen.findByRole("button", { name: /normal/i }),
    ).toBeInTheDocument();
  });
});
