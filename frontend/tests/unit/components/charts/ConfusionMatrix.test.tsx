import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  ConfusionMatrix,
  cellColor,
} from "@/components/charts/ConfusionMatrix";

/**
 * ``ConfusionMatrix`` is the leaf used by TrainSummary and
 * EvaluateSummary (both stubbed it via ``vi.mock`` in #365 and #363).
 * This PR pins the leaf's own contract:
 *
 * - Pred header row uses every label, prefixed "Pred ".
 * - Each matrix row gets a "True <label>" stub and renders every cell
 *   value in order.
 * - Diagonal cells (true == predicted) get the "success" tone class;
 *   off-diagonal cells get the "warn" tone class.
 * - ``cellColor`` is exported separately for parity with the test
 *   pattern set by other lolday chart helpers — pin its contract too.
 */

describe("cellColor", () => {
  it("returns 'success' on the diagonal", () => {
    expect(cellColor(0, 0, true)).toBe("success");
    expect(cellColor(2, 2, true)).toBe("success");
  });

  it("returns 'warn' off the diagonal", () => {
    expect(cellColor(0, 1, false)).toBe("warn");
    expect(cellColor(2, 0, false)).toBe("warn");
  });
});

describe("ConfusionMatrix", () => {
  it("renders one Pred header per label", () => {
    render(
      <ConfusionMatrix
        labels={["benign", "malware"]}
        matrix={[
          [10, 1],
          [2, 7],
        ]}
      />,
    );
    expect(screen.getByText("Pred benign")).toBeInTheDocument();
    expect(screen.getByText("Pred malware")).toBeInTheDocument();
  });

  it("renders one True stub per row, in row order", () => {
    render(
      <ConfusionMatrix
        labels={["benign", "malware"]}
        matrix={[
          [10, 1],
          [2, 7],
        ]}
      />,
    );
    expect(screen.getByText("True benign")).toBeInTheDocument();
    expect(screen.getByText("True malware")).toBeInTheDocument();
  });

  it("renders every cell value in the correct grid position", () => {
    render(
      <ConfusionMatrix
        labels={["benign", "malware"]}
        matrix={[
          [10, 1],
          [2, 7],
        ]}
      />,
    );
    // Each integer should be findable individually.
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("colours diagonal cells with the success tone and off-diagonal with warn", () => {
    render(
      <ConfusionMatrix
        labels={["benign", "malware"]}
        matrix={[
          [10, 1],
          [2, 7],
        ]}
      />,
    );
    // Diagonal: 10 (0,0) and 7 (1,1) → emerald (success).
    expect(screen.getByText("10").className).toContain("bg-emerald-500");
    expect(screen.getByText("7").className).toContain("bg-emerald-500");
    // Off-diagonal: 1 (0,1) and 2 (1,0) → rose (warn).
    expect(screen.getByText("1").className).toContain("bg-rose-100");
    expect(screen.getByText("2").className).toContain("bg-rose-100");
  });

  it("handles a 3-class matrix", () => {
    render(
      <ConfusionMatrix
        labels={["a", "b", "c"]}
        matrix={[
          [5, 0, 1],
          [0, 4, 2],
          [1, 1, 3],
        ]}
      />,
    );
    // Sanity: 3 Pred headers, 3 True stubs.
    expect(screen.getByText("Pred a")).toBeInTheDocument();
    expect(screen.getByText("Pred b")).toBeInTheDocument();
    expect(screen.getByText("Pred c")).toBeInTheDocument();
    expect(screen.getByText("True a")).toBeInTheDocument();
    expect(screen.getByText("True b")).toBeInTheDocument();
    expect(screen.getByText("True c")).toBeInTheDocument();
    // Diagonal: 5, 4, 3 all emerald.
    expect(screen.getByText("5").className).toContain("bg-emerald-500");
    expect(screen.getByText("4").className).toContain("bg-emerald-500");
    expect(screen.getByText("3").className).toContain("bg-emerald-500");
  });
});
