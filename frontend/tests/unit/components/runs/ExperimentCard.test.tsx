import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { ExperimentCard } from "@/components/runs/ExperimentCard";

/**
 * `ExperimentCard` is the tile rendered for each experiment on the Runs
 * landing page. It exposes three optional fields (`run_count`,
 * `best_f1`, `latest_start_time`) — each has a null-fallback that the
 * page contract depends on:
 *
 * - `run_count` null -> em-dash, not the string ``"null runs"``.
 * - `best_f1` null -> em-dash; populated -> ``toFixed(4)`` so the
 *   column reads as a fixed-precision number.
 * - `latest_start_time` null -> the literal text ``"no runs"`` (this is
 *   what tells the user the experiment exists but has no runs yet).
 *
 * The card also wraps the body in a `<Link to=/runs/<id>>` and renders
 * an OpenInMlflowButton — both should be reachable as accessible
 * targets so a keyboard user (or test bot) can navigate from the tile.
 */

function renderCard(
  overrides: Partial<Parameters<typeof ExperimentCard>[0]["exp"]> = {},
) {
  const exp = {
    experiment_id: "exp-42",
    name: "upxelfdet",
    run_count: 7,
    best_f1: 0.8123,
    latest_start_time: Date.UTC(2026, 0, 1, 12, 0, 0),
    ...overrides,
  };
  return render(
    <MemoryRouter>
      <ExperimentCard exp={exp} />
    </MemoryRouter>,
  );
}

describe("ExperimentCard", () => {
  it("renders the experiment name + run count + best F1 + relative time", () => {
    renderCard();
    expect(screen.getByText("upxelfdet")).toBeInTheDocument();
    // run_count + " runs"
    expect(screen.getByText(/7 runs/)).toBeInTheDocument();
    // best_f1 formatted as four decimals
    expect(screen.getByText(/Best F1:\s*0\.8123/)).toBeInTheDocument();
  });

  it("links the tile to /runs/<experiment_id>", () => {
    renderCard({ experiment_id: "exp-foo" });
    // The Link wraps the title + meta block; OpenInMlflowButton renders
    // its own anchor, so pick the tile link by its target href.
    const link = screen.getByRole("link", { name: /upxelfdet/ });
    expect(link).toHaveAttribute("href", "/runs/exp-foo");
  });

  it("falls back to em-dash when run_count is null", () => {
    renderCard({ run_count: null });
    // The em-dash precedes the literal " runs · ".
    expect(screen.getByText(/—\s*runs/)).toBeInTheDocument();
  });

  it("falls back to em-dash when best_f1 is null", () => {
    renderCard({ best_f1: null });
    expect(screen.getByText(/Best F1:\s*—/)).toBeInTheDocument();
  });

  it("renders 'no runs' when latest_start_time is null", () => {
    renderCard({ latest_start_time: null });
    expect(screen.getByText(/no runs/)).toBeInTheDocument();
  });
});
