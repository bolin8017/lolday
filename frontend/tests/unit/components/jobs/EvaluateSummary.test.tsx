import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { EvaluateSummary } from "@/components/jobs/EvaluateSummary";

type JobRead = components["schemas"]["JobRead"];

/**
 * `EvaluateSummary` is the per-job composition tile for Evaluate-job
 * detail pages. It conditionally renders:
 *
 * - `SourceModelCard` when the job has a `source_model_version_id`.
 * - The Per-class metrics card when `summary_metrics.per_class` is set.
 * - The Confusion matrix card when `summary_metrics.confusion_matrix`
 *   has both `labels` and `matrix`.
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
vi.mock("@/components/jobs/MetricsTable", () => ({
  MetricsTable: ({ metrics }: { metrics: Record<string, number> }) => (
    <div data-testid="metrics-table">{Object.keys(metrics).length}</div>
  ),
}));
vi.mock("@/components/jobs/PerClassMetrics", () => ({
  PerClassMetrics: ({ positiveClass }: { positiveClass?: string }) => (
    <div data-testid="per-class-metrics">{positiveClass ?? ""}</div>
  ),
}));
vi.mock("@/components/charts/ConfusionMatrix", () => ({
  ConfusionMatrix: ({
    labels,
    matrix,
  }: {
    labels: string[];
    matrix: number[][];
  }) => (
    <div data-testid="confusion-matrix">
      {labels.length}x{matrix.length}
    </div>
  ),
}));
vi.mock("@/components/jobs/ResolvedConfigCard", () => ({
  ResolvedConfigCard: () => <div data-testid="resolved-config-card" />,
}));

function makeJob(over: Partial<JobRead> = {}): JobRead {
  return {
    id: "job-1",
    type: "evaluate",
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
      <EvaluateSummary job={job} />
    </MemoryRouter>,
  );
}

describe("EvaluateSummary", () => {
  it("renders only the Evaluation metrics + ResolvedConfig tiles for a bare job", () => {
    renderWithRouter(makeJob());
    expect(screen.getByText("Evaluation metrics")).toBeInTheDocument();
    expect(screen.getByTestId("metrics-table")).toBeInTheDocument();
    expect(screen.getByTestId("resolved-config-card")).toBeInTheDocument();
    expect(screen.queryByTestId("source-model-card")).not.toBeInTheDocument();
    expect(screen.queryByText("Per-class metrics")).not.toBeInTheDocument();
    expect(screen.queryByText("Confusion matrix")).not.toBeInTheDocument();
  });

  it("renders SourceModelCard when source_model_version_id is set", () => {
    renderWithRouter(makeJob({ source_model_version_id: "mv-42" }));
    expect(screen.getByTestId("source-model-card")).toHaveTextContent("mv-42");
  });

  it("renders the Per-class metrics card when summary_metrics.per_class is set", () => {
    renderWithRouter(
      makeJob({
        summary_metrics: {
          metrics: { accuracy: 0.9 },
          per_class: { malware: { precision: 0.9, recall: 0.8 } },
        },
        positive_class: "malware",
      }),
    );
    expect(screen.getByText("Per-class metrics")).toBeInTheDocument();
    expect(screen.getByTestId("per-class-metrics")).toHaveTextContent(
      "malware",
    );
  });

  it("renders the Confusion matrix card when labels + matrix are set", () => {
    renderWithRouter(
      makeJob({
        summary_metrics: {
          confusion_matrix: {
            labels: ["benign", "malware"],
            matrix: [
              [10, 1],
              [2, 7],
            ],
          },
        },
      }),
    );
    expect(screen.getByText("Confusion matrix")).toBeInTheDocument();
    expect(screen.getByTestId("confusion-matrix")).toHaveTextContent("2x2");
  });

  it("hides the Confusion matrix card when labels or matrix is missing", () => {
    renderWithRouter(
      makeJob({
        summary_metrics: {
          // `matrix` missing — partial confusion_matrix object should not render the card
          confusion_matrix: { labels: ["benign", "malware"] },
        },
      }),
    );
    expect(screen.queryByText("Confusion matrix")).not.toBeInTheDocument();
  });

  it("forwards the metrics map to MetricsTable", () => {
    renderWithRouter(
      makeJob({
        summary_metrics: {
          metrics: { accuracy: 0.95, f1: 0.91, precision: 0.93 },
        },
      }),
    );
    // The stubbed MetricsTable renders the key count.
    expect(screen.getByTestId("metrics-table")).toHaveTextContent("3");
  });
});
