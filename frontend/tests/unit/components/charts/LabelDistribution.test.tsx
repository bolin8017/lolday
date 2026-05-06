import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LabelDistribution } from "@/components/charts/LabelDistribution";

describe("<LabelDistribution>", () => {
  it("renders empty state when data is empty", () => {
    render(<LabelDistribution data={{}} />);
    expect(screen.getByText("No label data.")).toBeInTheDocument();
  });

  it("renders both classes with counts and percentages", () => {
    render(<LabelDistribution data={{ Malware: 60, Benign: 40 }} />);
    // "Malware" appears twice: once in the donut center and once in the legend
    expect(screen.getAllByText("Malware")).toHaveLength(2);
    expect(screen.getByText("Benign")).toBeInTheDocument();
    // counts rendered as standalone text nodes in legend
    expect(screen.getByText("60")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    // percentages: {pct}% and {dominantPct}% render as separate React text nodes;
    // use regex to match the full visible string across siblings
    expect(screen.getByText(/^60\.0%$/)).toBeInTheDocument();
    expect(screen.getByText(/^40\.0%$/)).toBeInTheDocument();
    expect(screen.getByText(/^60%$/)).toBeInTheDocument(); // donut center (rounded)
  });

  it("falls back to neutral color for unknown labels", () => {
    // Smoke test: should render without throwing and include the unknown label.
    render(<LabelDistribution data={{ Malware: 10, Suspicious: 5 }} />);
    expect(screen.getByText("Suspicious")).toBeInTheDocument();
  });
});
