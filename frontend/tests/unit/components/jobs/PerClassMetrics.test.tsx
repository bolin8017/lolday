import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PerClassMetrics } from "@/components/jobs/PerClassMetrics";

/**
 * `PerClassMetrics` renders the per-class precision/recall/F1/support
 * table on the Evaluate / Predict job-detail pages. Three contracts that
 * a refactor must not break:
 *
 * 1. The positive class — when supplied — must appear FIRST so the user
 *   sees the metric that drives the F1 headline at the top of the table.
 *   The rest are alphabetical.
 * 2. All three rate metrics are formatted with `toFixed(4)` so the table
 *   columns stay width-stable across rows.
 * 3. `support` (an integer count) is rendered as-is, NOT toFixed'd —
 *   "1503" should never appear as "1503.0000".
 */
describe("PerClassMetrics", () => {
  it("orders the positive class first and the rest alphabetically", () => {
    render(
      <PerClassMetrics
        positiveClass="malware"
        perClass={{
          zealot: { precision: 0.5, recall: 0.5, f1: 0.5, support: 10 },
          benign: { precision: 0.9, recall: 0.9, f1: 0.9, support: 100 },
          malware: { precision: 0.8, recall: 0.8, f1: 0.8, support: 50 },
        }}
      />,
    );
    const rows = screen.getAllByRole("row");
    // [header, malware, benign, zealot]
    expect(rows).toHaveLength(4);
    expect(within(rows[1]).getByText("malware")).toBeInTheDocument();
    expect(within(rows[2]).getByText("benign")).toBeInTheDocument();
    expect(within(rows[3]).getByText("zealot")).toBeInTheDocument();
  });

  it("falls back to plain alphabetical when no positiveClass is supplied", () => {
    render(
      <PerClassMetrics
        perClass={{
          zealot: { precision: 0.5, recall: 0.5, f1: 0.5, support: 10 },
          benign: { precision: 0.9, recall: 0.9, f1: 0.9, support: 100 },
        }}
      />,
    );
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]).getByText("benign")).toBeInTheDocument();
    expect(within(rows[2]).getByText("zealot")).toBeInTheDocument();
  });

  it("formats precision/recall/F1 with toFixed(4) and renders support as a bare integer", () => {
    render(
      <PerClassMetrics
        perClass={{
          benign: {
            precision: 0.93333,
            recall: 0.7,
            f1: 0.8,
            support: 1503,
          },
        }}
      />,
    );
    expect(screen.getByText("0.9333")).toBeInTheDocument(); // precision
    expect(screen.getByText("0.7000")).toBeInTheDocument(); // recall
    expect(screen.getByText("0.8000")).toBeInTheDocument(); // f1
    // Support is NOT toFixed'd — an integer count rendered as-is.
    expect(screen.getByText("1503")).toBeInTheDocument();
    expect(screen.queryByText("1503.0000")).not.toBeInTheDocument();
  });

  it("renders an empty body when perClass is an empty object", () => {
    render(<PerClassMetrics perClass={{}} />);
    // Header row only.
    expect(screen.getAllByRole("row")).toHaveLength(1);
  });
});
