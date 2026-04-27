import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FinalMetricsTile } from "@/components/jobs/FinalMetricsTile";

describe("FinalMetricsTile", () => {
  it("renders dash when summary_metrics is null", () => {
    render(<FinalMetricsTile summaryMetrics={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders dash when summary_metrics has empty metrics", () => {
    render(<FinalMetricsTile summaryMetrics={{ metrics: {}, confusion_matrix: null }} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders first two metrics inline + +N more for additional", () => {
    render(<FinalMetricsTile summaryMetrics={{
      metrics: { acc: 0.987, f1: 0.94, precision: 0.99, recall: 0.92 },
      confusion_matrix: null,
    }} />);
    // First 2 metrics shown by stable iteration order (Object.entries preserves insertion order)
    expect(screen.getByText(/acc:/)).toBeInTheDocument();
    expect(screen.getByText(/f1:/)).toBeInTheDocument();
    expect(screen.getByText(/\+2/)).toBeInTheDocument();
  });

  it("renders all metrics inline if 2 or fewer (no +N)", () => {
    render(<FinalMetricsTile summaryMetrics={{
      metrics: { acc: 0.99 },
      confusion_matrix: null,
    }} />);
    expect(screen.queryByText(/\+\d/)).toBeNull();
    expect(screen.getByText(/acc:/)).toBeInTheDocument();
  });

  it("formats numeric metrics to 3 decimal places", () => {
    render(<FinalMetricsTile summaryMetrics={{
      metrics: { acc: 0.123456 },
      confusion_matrix: null,
    }} />);
    expect(screen.getByText(/0\.123/)).toBeInTheDocument();
  });

  it("handles undefined summary_metrics like null", () => {
    render(<FinalMetricsTile summaryMetrics={undefined} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
