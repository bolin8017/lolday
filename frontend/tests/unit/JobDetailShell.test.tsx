import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { JobDetailShell } from "@/components/jobs/JobDetailShell";
import type { components } from "@/api/schema";

const cancelMutate = vi.fn();
vi.mock("@/api/queries/jobs", async () => {
  const mod =
    await vi.importActual<typeof import("@/api/queries/jobs")>(
      "@/api/queries/jobs",
    );
  return {
    ...mod,
    useCancelJob: vi.fn(() => ({ mutate: cancelMutate, isPending: false })),
    usePatchJob: vi.fn(() => ({
      mutate: vi.fn(),
      isPending: false,
      isError: false,
      error: null,
    })),
  };
});

vi.mock("@/api/queries/cluster", () => ({
  useJobQueuePosition: vi.fn(() => ({ data: null })),
}));

type JobRead = components["schemas"]["JobRead"];

const baseJob = {
  id: "11111111-1111-1111-1111-111111111111",
  type: "train",
  status: "succeeded",
  detector_version_id: "22222222-2222-2222-2222-222222222222",
  owner_id: "33333333-3333-3333-3333-333333333333",
  mlflow_run_id: "run-abc",
  mlflow_experiment_id: "exp-1",
  k8s_job_name: null,
  failure_reason: null,
  submitted_at: "2026-05-01T00:00:00Z",
  started_at: null,
  finished_at: null,
  train_dataset_id: null,
  test_dataset_id: null,
  predict_dataset_id: null,
  source_model_version_id: null,
  resolved_config: {},
  log_tail: null,
  resource_profile: "tiny",
} as unknown as JobRead;

const renderShell = (job: JobRead) => {
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
};

describe("JobDetailShell", () => {
  beforeEach(() => {
    cancelMutate.mockClear();
  });

  it("renders Open in MLflow button when run id and experiment id are set", () => {
    renderShell(baseJob);
    // i18n key matches "Open in MLflow" in en (default fallbackLng).
    expect(
      screen.getByRole("link", { name: /open in mlflow/i }),
    ).toBeInTheDocument();
  });

  it("does not render Open in MLflow when run id is missing", () => {
    const job = { ...baseJob, mlflow_run_id: null } as JobRead;
    renderShell(job);
    expect(screen.queryByRole("link", { name: /open in mlflow/i })).toBeNull();
  });

  it("does not render Open in MLflow when experiment id is missing", () => {
    const job = { ...baseJob, mlflow_experiment_id: null } as JobRead;
    renderShell(job);
    expect(screen.queryByRole("link", { name: /open in mlflow/i })).toBeNull();
  });

  it("Open in MLflow link points at the MLflow run UI", () => {
    renderShell(baseJob);
    const link = screen.getByRole("link", { name: /open in mlflow/i });
    expect(link).toHaveAttribute(
      "href",
      "/mlflow/#/experiments/exp-1/runs/run-abc",
    );
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("hides Cancel button for terminal job status", () => {
    renderShell(baseJob);
    expect(screen.queryByRole("button", { name: /cancel/i })).toBeNull();
  });

  it("shows Cancel button for non-terminal job status and dispatches mutate on click", async () => {
    const job = { ...baseJob, status: "running" } as JobRead;
    renderShell(job);
    const cancelBtn = screen.getByRole("button", { name: /cancel/i });
    expect(cancelBtn).toBeInTheDocument();
    await userEvent.click(cancelBtn);
    expect(cancelMutate).toHaveBeenCalledWith(job.id);
  });

  it("Clone button navigates to /jobs/new with from=<jobId>", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    let captured = "";
    function LocationProbe() {
      const loc = useLocation();
      captured = `${loc.pathname}${loc.search}`;
      return null;
    }
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/jobs/x"]}>
          <Routes>
            <Route
              path="/jobs/x"
              element={
                <JobDetailShell job={baseJob}>
                  <div data-testid="children" />
                </JobDetailShell>
              }
            />
            <Route path="/jobs/new" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: /clone/i }));
    expect(captured).toBe(`/jobs/new?from=${baseJob.id}`);
  });
});
