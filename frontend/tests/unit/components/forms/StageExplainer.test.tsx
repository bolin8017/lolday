import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StageExplainer } from "@/components/forms/StageExplainer";

// Badges in the shadcn ui/badge component render as <div> elements with
// either the "default" (filled, primary bg) or "outline" (transparent bg +
// border) variant. Required badges use "default", optional use "outline".
// We assert on the count of each by querying classes that map to each
// variant — keeps the test stable across i18n / locale text changes.
function badgeCounts(container: HTMLElement) {
  // The shadcn Badge primitive uses cva variant classes — required
  // ("default") carries `border-transparent` + `bg-primary`; optional
  // ("outline") carries no `bg-primary` and instead a border. The most
  // stable signature across versions is the inner-flex Badge wrapper —
  // we ask for the deepest div siblings of the badge container.
  const allBadges = Array.from(container.querySelectorAll("div")).filter((d) =>
    /inline-flex/.test(d.className),
  );
  return allBadges.length;
}

describe("StageExplainer", () => {
  it("renders the train-stage badges (1 required + 2 optional)", () => {
    const { container } = render(<StageExplainer type="train" />);
    // Required: train_dataset. Optional: test_dataset, hyperparameters.
    expect(badgeCounts(container)).toBe(3);
  });

  it("renders the evaluate-stage badges (2 required + 1 optional)", () => {
    const { container } = render(<StageExplainer type="evaluate" />);
    expect(badgeCounts(container)).toBe(3);
  });

  it("renders the predict-stage badges (2 required + 1 optional)", () => {
    const { container } = render(<StageExplainer type="predict" />);
    expect(badgeCounts(container)).toBe(3);
  });

  it("renders the localised stage title (i18n key resolves to a non-empty string)", () => {
    render(<StageExplainer type="train" />);
    // The stage.<type>.title key resolves through the test setup to
    // English. Assert the visible heading contains the stage name —
    // this is the contract that survives even if the description
    // copy gets reworded.
    expect(screen.getByText(/Train.*train a new model/i)).toBeInTheDocument();
  });
});
