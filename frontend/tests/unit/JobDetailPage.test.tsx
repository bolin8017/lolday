import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";

import JobDetailPage from "@/routes/_authed.jobs.$id";
import type { components } from "@/api/schema.gen";

type JobRead = components["schemas"]["JobRead"];

vi.mock("@/api/queries/jobs", async () => {
  const mod =
    await vi.importActual<typeof import("@/api/queries/jobs")>(
      "@/api/queries/jobs",
    );
  return {
    ...mod,
    useJob: vi.fn(),
    useJobLogs: vi.fn(),
    useCancelJob: vi.fn(() => ({ mutate: vi.fn() })),
  };
});

vi.mock("@/api/queries/cluster", () => ({
  useJobQueuePosition: vi.fn(() => ({ data: null })),
}));

// The Train/Evaluate/Predict summary components fan out into many other
// queries. We're only asserting the tabs / open-run absence here, so stub
// them out to keep the test focused.
vi.mock("@/components/jobs/TrainSummary", () => ({
  TrainSummary: () => <div data-testid="train-summary" />,
}));
vi.mock("@/components/jobs/EvaluateSummary", () => ({
  EvaluateSummary: () => <div data-testid="evaluate-summary" />,
}));
vi.mock("@/components/jobs/PredictSummary", () => ({
  PredictSummary: () => <div data-testid="predict-summary" />,
}));
vi.mock("@/components/common/ArtifactTree", () => ({
  ArtifactTree: () => <div data-testid="artifact-tree" />,
}));

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

const renderPage = () => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter
        initialEntries={["/jobs/11111111-1111-1111-1111-111111111111"]}
      >
        <Routes>
          <Route path="/jobs/:id" element={<JobDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe("JobDetailPage tabs", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const { useJob, useJobLogs, useCancelJob } =
      await import("@/api/queries/jobs");
    (useJob as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: baseJob,
    });
    (useJobLogs as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: "",
    });
    (useCancelJob as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      mutate: vi.fn(),
    });
  });

  it("does not render an 'Open run' tab", () => {
    renderPage();
    expect(screen.queryByText(/open run/i)).toBeNull();
  });

  it("renders only Summary, Logs, Artifacts tabs", () => {
    renderPage();
    expect(screen.getByRole("tab", { name: /summary/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /logs/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /artifacts/i })).toBeInTheDocument();
    // 3 tabs total — no fourth.
    expect(screen.getAllByRole("tab")).toHaveLength(3);
  });
});
