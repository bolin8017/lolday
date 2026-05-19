import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FinalMetricsTile } from "@/components/jobs/FinalMetricsTile";

/**
 * `FinalMetricsTile` is the inline summary chip on the Jobs list table
 * (`_authed.jobs._index.tsx` row column). The three branches that drive
 * the visible cell:
 *
 * 1. No metrics (null / undefined / empty object) -> em-dash placeholder.
 * 2. <=2 metrics -> all rendered, no overflow indicator.
 * 3. >2 metrics -> first two rendered + a `+N` overflow chip showing the
 *    remainder.
 *
 * Numbers are formatted with `toFixed(3)` so the table column stays
 * width-stable across rows.
 */
describe("FinalMetricsTile", () => {
  it("renders an em-dash when summaryMetrics is null", () => {
    render(<FinalMetricsTile summaryMetrics={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders an em-dash when summaryMetrics is undefined", () => {
    render(<FinalMetricsTile summaryMetrics={undefined} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders an em-dash when metrics is an empty object", () => {
    render(<FinalMetricsTile summaryMetrics={{ metrics: {} }} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders both metrics inline when there are exactly two", () => {
    render(
      <FinalMetricsTile
        summaryMetrics={{ metrics: { accuracy: 0.953, f1: 0.812 } }}
      />,
    );
    expect(screen.getByText("accuracy: 0.953")).toBeInTheDocument();
    expect(screen.getByText("f1: 0.812")).toBeInTheDocument();
    // No overflow chip rendered.
    expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
  });

  it("shows the first two metrics plus a +N overflow chip when there are more", () => {
    render(
      <FinalMetricsTile
        summaryMetrics={{
          metrics: {
            accuracy: 0.9533333,
            f1: 0.812,
            precision: 0.701,
            recall: 0.65,
          },
        }}
      />,
    );
    // First two shown; the toFixed(3) contract keeps column width stable.
    expect(screen.getByText("accuracy: 0.953")).toBeInTheDocument();
    expect(screen.getByText("f1: 0.812")).toBeInTheDocument();
    // The last two are folded into the overflow chip.
    expect(screen.queryByText(/precision/)).not.toBeInTheDocument();
    expect(screen.queryByText(/recall/)).not.toBeInTheDocument();
    expect(screen.getByText("+2")).toBeInTheDocument();
  });

  it("formats integer metric values with three trailing zeros", () => {
    // Sanity-check the toFixed(3) contract on non-decimal inputs — the
    // table column relies on every cell having the same numeric width.
    render(<FinalMetricsTile summaryMetrics={{ metrics: { count: 7 } }} />);
    expect(screen.getByText("count: 7.000")).toBeInTheDocument();
  });
});
