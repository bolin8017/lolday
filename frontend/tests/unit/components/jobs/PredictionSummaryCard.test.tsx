import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PredictionSummaryCard } from "@/components/jobs/PredictionSummaryCard";

/**
 * `PredictionSummaryCard` renders the headline tile for Predict-job
 * detail pages: total samples, optional duration, distribution stacked
 * bar, and a per-class legend grid. Four contracts pin the user-visible
 * shape:
 *
 * - `summary === null` -> "Prediction summary not available" hint, so
 *   the user sees the reason rather than an empty card.
 * - `duration_seconds === null` -> the Duration block is suppressed
 *   (a legacy run without timing should not show "null s").
 * - Class ordering: the `positiveClass` row sits first; the rest sort
 *   by descending count. This mirrors PerClassMetrics so the two cards
 *   read consistently.
 * - `total === 0` (or empty distribution) -> bars compute to 0% (no
 *   NaN%), so the card still renders without DOM warnings.
 */
describe("PredictionSummaryCard", () => {
  it("renders a 'not available' hint when summary is null", () => {
    render(<PredictionSummaryCard summary={null} />);
    expect(
      screen.getByText(/Prediction summary not available/),
    ).toBeInTheDocument();
    // The title is rendered in every state.
    expect(screen.getByText("Predictions")).toBeInTheDocument();
  });

  it("renders total + duration when duration_seconds is set", () => {
    render(
      <PredictionSummaryCard
        summary={{
          total: 12345,
          distribution: { benign: 12000, malware: 345 },
          duration_seconds: 7.83,
        }}
        positiveClass="malware"
      />,
    );
    // Total uses toLocaleString so it carries thousands separators.
    expect(screen.getByText(/12,345|12 345|12.345/)).toBeInTheDocument();
    expect(screen.getByText(/7\.8s/)).toBeInTheDocument();
  });

  it("suppresses the Duration block when duration_seconds is null", () => {
    render(
      <PredictionSummaryCard
        summary={{
          total: 100,
          distribution: { benign: 100 },
          duration_seconds: null,
        }}
      />,
    );
    expect(screen.queryByText(/Duration/)).not.toBeInTheDocument();
    expect(screen.getByText(/Total samples/)).toBeInTheDocument();
  });

  it("orders the positive class first then sorts the rest by descending count", () => {
    render(
      <PredictionSummaryCard
        summary={{
          total: 100,
          distribution: { benign: 70, suspicious: 20, malware: 10 },
          duration_seconds: 1,
        }}
        positiveClass="malware"
      />,
    );
    // Find the legend cells (the per-class grid). Each cell carries the
    // class name followed by ": <count>" — read the order.
    const legendItems = screen.getAllByText(/^benign|^suspicious|^malware/);
    expect(legendItems[0]).toHaveTextContent("malware");
    expect(legendItems[1]).toHaveTextContent("benign");
    expect(legendItems[2]).toHaveTextContent("suspicious");
  });

  it("computes 0% bars when total is 0 (no NaN%)", () => {
    const { container } = render(
      <PredictionSummaryCard
        summary={{
          total: 0,
          distribution: { benign: 0 },
          duration_seconds: 0,
        }}
      />,
    );
    // The stacked bar's first segment must carry width: 0%, not NaN%.
    const bar = container.querySelector(
      "[style*='width']",
    ) as HTMLElement | null;
    expect(bar).not.toBeNull();
    expect(bar!.getAttribute("style")).toMatch(/width:\s*0%/);
  });
});
