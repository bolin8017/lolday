import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { JobDetailShell } from "@/components/jobs/JobDetailShell";
import type { components } from "@/api/schema";

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
});
