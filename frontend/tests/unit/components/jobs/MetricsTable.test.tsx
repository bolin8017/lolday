import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MetricsTable } from "@/components/jobs/MetricsTable";

/**
 * `MetricsTable` is the leaf card grid used by TrainSummary,
 * EvaluateSummary, and the final-metrics tile. Pure component, no
 * data dependencies — pinned end-to-end here.
 *
 * Behaviours covered:
 *
 * - Empty-state message when the metrics map is empty.
 * - Standard-order ordering: accuracy → precision → recall → f1 first,
 *   regardless of insertion order.
 * - Non-standard keys appear after the standard ones, alphabetised.
 * - Numbers render to 4 decimal places.
 * - Known keys get human labels (ROC AUC, PR AUC, F1).
 * - Unknown keys are humanised (snake_case → Title Case).
 * - Non-numeric values are filtered out.
 */

describe("MetricsTable", () => {
  it("shows the empty-state message when there are no metrics", () => {
    render(<MetricsTable metrics={{}} />);
    expect(
      screen.getByText("No metrics recorded for this job."),
    ).toBeInTheDocument();
  });

  it("orders standard metrics in accuracy/precision/recall/f1 order", () => {
    render(
      <MetricsTable
        metrics={{
          f1: 0.91,
          recall: 0.88,
          accuracy: 0.95,
          precision: 0.93,
        }}
      />,
    );
    const cards = screen.getAllByTestId("metric-card");
    const names = cards.map((c) => c.getAttribute("data-name"));
    expect(names).toEqual(["accuracy", "precision", "recall", "f1"]);
  });

  it("places non-standard keys after standard ones, alphabetised", () => {
    render(
      <MetricsTable
        metrics={{
          roc_auc: 0.98,
          pr_auc: 0.95,
          accuracy: 0.9,
          custom_metric: 0.5,
        }}
      />,
    );
    const cards = screen.getAllByTestId("metric-card");
    const names = cards.map((c) => c.getAttribute("data-name"));
    // accuracy (standard) first, then custom_metric / pr_auc / roc_auc alphabetised
    expect(names).toEqual(["accuracy", "custom_metric", "pr_auc", "roc_auc"]);
  });

  it("renders each metric value to exactly 4 decimal places", () => {
    render(<MetricsTable metrics={{ accuracy: 0.5 }} />);
    const card = screen.getByTestId("metric-card");
    expect(within(card).getByText("0.5000")).toBeInTheDocument();
  });

  it("renders the human label for known keys", () => {
    render(
      <MetricsTable
        metrics={{ accuracy: 0.9, roc_auc: 0.95, pr_auc: 0.93, f1_score: 0.9 }}
      />,
    );
    expect(screen.getByText("Accuracy")).toBeInTheDocument();
    expect(screen.getByText("ROC AUC")).toBeInTheDocument();
    expect(screen.getByText("PR AUC")).toBeInTheDocument();
    expect(screen.getByText("F1")).toBeInTheDocument();
  });

  it("humanises unknown snake_case keys into Title Case", () => {
    render(<MetricsTable metrics={{ mean_absolute_error: 0.1 }} />);
    expect(screen.getByText("Mean Absolute Error")).toBeInTheDocument();
  });

  it("filters out non-numeric values entirely", () => {
    render(
      <MetricsTable
        metrics={{
          accuracy: 0.9,
          // Caller may pass a malformed payload; the component must
          // not render a card for the bad value (and must not crash).
          junk: "oops" as unknown as number,
        }}
      />,
    );
    const cards = screen.getAllByTestId("metric-card");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toHaveAttribute("data-name", "accuracy");
  });
});
