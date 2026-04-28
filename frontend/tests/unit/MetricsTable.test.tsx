import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MetricsTable } from "@/components/jobs/MetricsTable";

describe("MetricsTable", () => {
  it("shows accuracy/precision/recall/f1 first, then alphabetical", () => {
    render(
      <MetricsTable
        metrics={{
          accuracy: 0.9,
          roc_auc: 0.95,
          custom_metric: 0.5,
          f1: 0.8,
          precision: 0.85,
        }}
      />,
    );
    const cards = screen.getAllByTestId("metric-card");
    const labels = cards.map((c) => c.getAttribute("data-name"));
    expect(labels).toEqual([
      "accuracy",
      "precision",
      "f1",
      "custom_metric",
      "roc_auc",
    ]);
  });

  it("renders ROC AUC humanized label", () => {
    render(<MetricsTable metrics={{ roc_auc: 0.95 }} />);
    expect(screen.getByText("ROC AUC")).toBeInTheDocument();
  });

  it("formats values to 4 decimal places", () => {
    render(<MetricsTable metrics={{ accuracy: 0.123456789 }} />);
    expect(screen.getByText("0.1235")).toBeInTheDocument();
  });

  it("renders empty state when no metrics", () => {
    render(<MetricsTable metrics={{}} />);
    expect(screen.getByText(/no metrics/i)).toBeInTheDocument();
  });
});
