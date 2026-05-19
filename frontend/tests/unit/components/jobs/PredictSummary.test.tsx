import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { PredictSummary } from "@/components/jobs/PredictSummary";

type JobRead = components["schemas"]["JobRead"];

/**
 * `PredictSummary` is the per-job composition tile for Predict-job
 * detail pages. It conditionally renders:
 *
 * - `SourceModelCard` when the job has a `source_model_version_id`.
 * - The Output card with a `Download predictions.csv` link when
 *   `mlflow_run_id` is set (links to the backend artifact route).
 *
 * Stub the leaf cards so the test stays focused on the composition
 * choices (which children render when) rather than re-validating the
 * already-tested leaf behaviour.
 */

vi.mock("@/components/jobs/SourceModelCard", () => ({
  SourceModelCard: ({
    sourceModelVersionId,
  }: {
    sourceModelVersionId: string;
  }) => <div data-testid="source-model-card">{sourceModelVersionId}</div>,
}));
vi.mock("@/components/jobs/PredictionSummaryCard", () => ({
  PredictionSummaryCard: () => <div data-testid="prediction-summary-card" />,
}));
vi.mock("@/components/jobs/ResolvedConfigCard", () => ({
  ResolvedConfigCard: () => <div data-testid="resolved-config-card" />,
}));

function makeJob(over: Partial<JobRead> = {}): JobRead {
  return {
    id: "job-1",
    type: "predict",
    status: "succeeded",
    source_model_version_id: null,
    mlflow_run_id: null,
    summary_metrics: null,
    positive_class: null,
    resolved_config: {},
    user_params: {},
    detector_defaults: {},
    ...over,
  } as JobRead;
}

function renderWithRouter(job: JobRead) {
  return render(
    <MemoryRouter>
      <PredictSummary job={job} />
    </MemoryRouter>,
  );
}

describe("PredictSummary", () => {
  it("renders only the PredictionSummary + ResolvedConfig tiles for a bare job", () => {
    renderWithRouter(makeJob());
    expect(screen.getByTestId("prediction-summary-card")).toBeInTheDocument();
    expect(screen.getByTestId("resolved-config-card")).toBeInTheDocument();
    // Neither SourceModelCard nor the Output card should appear.
    expect(screen.queryByTestId("source-model-card")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /Download/ }),
    ).not.toBeInTheDocument();
  });

  it("renders SourceModelCard when source_model_version_id is set", () => {
    renderWithRouter(makeJob({ source_model_version_id: "mv-42" }));
    expect(screen.getByTestId("source-model-card")).toHaveTextContent("mv-42");
  });

  it("renders the Output download link when mlflow_run_id is set", () => {
    renderWithRouter(makeJob({ mlflow_run_id: "run-abc" }));
    const link = screen.getByRole("link", {
      name: /Download predictions\.csv/i,
    });
    expect(link).toHaveAttribute(
      "href",
      "/api/v1/runs/run-abc/artifacts/download?path=predictions.csv",
    );
    expect(link).toHaveAttribute("download", "predictions.csv");
  });

  it("hides the Output card when mlflow_run_id is null", () => {
    renderWithRouter(makeJob({ source_model_version_id: "mv-1" }));
    expect(screen.queryByText("Output")).not.toBeInTheDocument();
  });
});
