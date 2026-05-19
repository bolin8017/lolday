import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { JobMetricChart } from "@/components/charts/JobMetricChart";
import type { MaldetEvent } from "@/hooks/useJobEvents";

/**
 * ``JobMetricChart`` is the live-metrics chart used by TrainSummary
 * (#365 stubbed it during composition testing). The component is a
 * thin wrapper around recharts whose interesting behaviour lives in
 * the ``metricsToSeries`` reducer:
 *
 * - Non-``metric`` events are filtered out.
 * - Missing ``step`` defaults to 0 (warm-up bucket).
 * - Missing ``name`` defaults to ``"value"``.
 * - ``NaN`` values are dropped.
 * - Multiple metrics on the same step collapse into one row.
 * - Output rows are sorted by ``step`` ascending.
 *
 * Recharts itself requires a non-zero parent size + ``ResizeObserver``
 * that jsdom does not provide. Mock the whole library to a set of
 * data-test-id stubs that expose the rendered shape.
 */

vi.mock("recharts", () => {
  return {
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="responsive-container">{children}</div>
    ),
    LineChart: ({
      data,
      children,
    }: {
      data: Record<string, unknown>[];
      children: React.ReactNode;
    }) => (
      <div data-testid="line-chart" data-points={JSON.stringify(data)}>
        {children}
      </div>
    ),
    Line: ({ dataKey }: { dataKey: string }) => (
      <div data-testid="line" data-key={dataKey} />
    ),
    XAxis: () => <div data-testid="x-axis" />,
    YAxis: () => <div data-testid="y-axis" />,
    CartesianGrid: () => <div data-testid="grid" />,
    Tooltip: () => <div data-testid="tooltip" />,
    Legend: () => <div data-testid="legend" />,
  };
});

function ev(over: Partial<MaldetEvent>): MaldetEvent {
  return { ts: "2026-05-19T00:00:00Z", kind: "metric", ...over };
}

function getPoints(): Record<string, unknown>[] {
  const chart = screen.getByTestId("line-chart");
  return JSON.parse(chart.getAttribute("data-points") ?? "[]");
}

function getLineKeys(): string[] {
  return screen
    .getAllByTestId("line")
    .map((l) => l.getAttribute("data-key") ?? "");
}

describe("JobMetricChart", () => {
  it("renders the empty-state message when there are no metric events", () => {
    render(<JobMetricChart events={[]} />);
    expect(screen.getByText("No metrics yet.")).toBeInTheDocument();
    expect(screen.queryByTestId("line-chart")).not.toBeInTheDocument();
  });

  it("ignores non-metric events", () => {
    render(
      <JobMetricChart
        events={[
          ev({ kind: "log", message: "running" } as Partial<MaldetEvent>),
          ev({ kind: "metric", step: 1, name: "loss", value: 0.5 }),
        ]}
      />,
    );
    expect(getPoints()).toEqual([{ step: 1, loss: 0.5 }]);
  });

  it("collapses multiple metrics on the same step into one row", () => {
    render(
      <JobMetricChart
        events={[
          ev({ step: 1, name: "loss", value: 0.5 }),
          ev({ step: 1, name: "accuracy", value: 0.9 }),
        ]}
      />,
    );
    const pts = getPoints();
    expect(pts).toHaveLength(1);
    expect(pts[0]).toMatchObject({ step: 1, loss: 0.5, accuracy: 0.9 });
  });

  it("sorts output rows by step ascending", () => {
    render(
      <JobMetricChart
        events={[
          ev({ step: 3, name: "loss", value: 0.2 }),
          ev({ step: 1, name: "loss", value: 0.5 }),
          ev({ step: 2, name: "loss", value: 0.3 }),
        ]}
      />,
    );
    const pts = getPoints();
    expect(pts.map((p) => p.step)).toEqual([1, 2, 3]);
  });

  it("drops events whose value is not numeric", () => {
    render(
      <JobMetricChart
        events={[
          ev({ step: 1, name: "loss", value: "oops" as unknown as number }),
          ev({ step: 1, name: "acc", value: 0.9 }),
        ]}
      />,
    );
    const pts = getPoints();
    expect(pts).toEqual([{ step: 1, acc: 0.9 }]);
  });

  it("buckets events without step into step=0 (warm-up)", () => {
    render(<JobMetricChart events={[ev({ name: "loss", value: 0.7 })]} />);
    const pts = getPoints();
    expect(pts).toEqual([{ step: 0, loss: 0.7 }]);
  });

  it("uses the name 'value' as fallback when the event lacks a name", () => {
    render(<JobMetricChart events={[ev({ step: 1, value: 0.42 })]} />);
    const pts = getPoints();
    expect(pts).toEqual([{ step: 1, value: 0.42 }]);
  });

  it("renders one Line per distinct metric name", () => {
    render(
      <JobMetricChart
        events={[
          ev({ step: 1, name: "loss", value: 0.5 }),
          ev({ step: 1, name: "accuracy", value: 0.9 }),
          ev({ step: 2, name: "loss", value: 0.4 }),
        ]}
      />,
    );
    const keys = getLineKeys();
    expect(keys.sort()).toEqual(["accuracy", "loss"]);
  });
});
