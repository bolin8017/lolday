import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { TrainSummary } from "@/components/jobs/TrainSummary";

type JobRead = components["schemas"]["JobRead"];

/**
 * `TrainSummary` is the per-job composition tile for Train-job detail
 * pages. Conditionally renders:
 *
 * - Per-class metrics card when `summary_metrics.per_class` is set.
 * - Confusion matrix card when both `labels` and `matrix` are set.
 * - Live metrics card when the events stream has a `metric` event
 *   with `step >= 1`, OR when `useJobEvents` surfaced an error.
 * - TrainedModelCard + ResolvedConfigCard always.
 *
 * Stub the leaf cards and `useJobEvents` so the test pins composition
 * decisions rather than re-validating already-tested leaves. The events
 * hook is the only non-leaf dependency — `vi.mock` replaces it per-test
 * by toggling the returned shape.
 */

const mockUseJobEvents = vi.fn();

vi.mock("@/hooks/useJobEvents", () => ({
  useJobEvents: (...args: unknown[]) => mockUseJobEvents(...args),
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
vi.mock("@/components/charts/JobMetricChart", () => ({
  JobMetricChart: () => <div data-testid="job-metric-chart" />,
}));
vi.mock("@/components/jobs/TrainedModelCard", () => ({
  TrainedModelCard: ({ jobId }: { jobId: string }) => (
    <div data-testid="trained-model-card">{jobId}</div>
  ),
}));
vi.mock("@/components/jobs/ResolvedConfigCard", () => ({
  ResolvedConfigCard: () => <div data-testid="resolved-config-card" />,
}));

function makeJob(over: Partial<JobRead> = {}): JobRead {
  return {
    id: "job-1",
    type: "train",
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
      <TrainSummary job={job} />
    </MemoryRouter>,
  );
}

describe("TrainSummary", () => {
  beforeEach(() => {
    mockUseJobEvents.mockReset();
    mockUseJobEvents.mockReturnValue({ events: [], error: null });
  });

  it("renders Final metrics + TrainedModelCard + ResolvedConfig for a bare job", () => {
    renderWithRouter(makeJob());
    expect(screen.getByText("Final metrics")).toBeInTheDocument();
    expect(screen.getByTestId("metrics-table")).toBeInTheDocument();
    expect(screen.getByTestId("trained-model-card")).toHaveTextContent("job-1");
    expect(screen.getByTestId("resolved-config-card")).toBeInTheDocument();
    expect(screen.queryByText("Per-class metrics")).not.toBeInTheDocument();
    expect(screen.queryByText("Confusion matrix")).not.toBeInTheDocument();
    expect(screen.queryByText("Live metrics")).not.toBeInTheDocument();
  });

  it("renders Per-class metrics card when summary_metrics.per_class is set", () => {
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

  it("renders Confusion matrix card when labels + matrix are set", () => {
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

  it("subscribes to live events when job status is non-terminal (queued)", () => {
    renderWithRouter(makeJob({ status: "queued_backend" }));
    expect(mockUseJobEvents).toHaveBeenCalledWith("job-1", true);
  });

  it("does not subscribe to live events when job status is terminal", () => {
    renderWithRouter(makeJob({ status: "succeeded" }));
    expect(mockUseJobEvents).toHaveBeenCalledWith("job-1", false);
  });

  it("renders Live metrics card with the chart when step>=1 metric events arrive", () => {
    mockUseJobEvents.mockReturnValue({
      events: [
        { ts: "2026-05-19T00:00:00Z", kind: "metric", step: 1, value: 0.5 },
      ],
      error: null,
    });
    renderWithRouter(makeJob({ status: "running" }));
    expect(screen.getByText("Live metrics")).toBeInTheDocument();
    expect(screen.getByTestId("job-metric-chart")).toBeInTheDocument();
  });

  it("renders the Live metrics card with the error message when useJobEvents reports an error", () => {
    mockUseJobEvents.mockReturnValue({
      events: [],
      error: "ws disconnected",
    });
    renderWithRouter(makeJob({ status: "running" }));
    expect(screen.getByText("Live metrics")).toBeInTheDocument();
    expect(screen.getByText("ws disconnected")).toBeInTheDocument();
    expect(screen.queryByTestId("job-metric-chart")).not.toBeInTheDocument();
  });

  it("hides the Live metrics card when only step=0 events arrive (warm-up only)", () => {
    mockUseJobEvents.mockReturnValue({
      events: [
        { ts: "2026-05-19T00:00:00Z", kind: "metric", step: 0, value: 0.0 },
      ],
      error: null,
    });
    renderWithRouter(makeJob({ status: "running" }));
    expect(screen.queryByText("Live metrics")).not.toBeInTheDocument();
  });
});
