import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { TrainedModelCard } from "@/components/jobs/TrainedModelCard";

/**
 * `TrainedModelCard` is one of three terminal-state tiles on the
 * Train-job detail page (next to `SourceModelCard` for evaluate /
 * predict jobs, and `ResolvedConfigCard` everywhere). The three
 * branches a user can land on:
 *
 * - Loading -> "Loading…" placeholder.
 * - Hook resolved to `null` (no ModelVersion yet — registration in
 *   flight or failed) -> the "Model not yet registered" hint, which
 *   tells the user to check backend logs rather than assume the job
 *   silently failed.
 * - Data present -> the registry-link target + stage.
 *
 * The `<Link to=/models/<owner>/<name>>` shape is the contract the
 * registry routes match on; pin it so a route refactor surfaces here.
 */

vi.mock("@/api/queries/models", () => ({
  useModelVersionForJob: vi.fn(),
}));

import { useModelVersionForJob } from "@/api/queries/models";

const mocked = vi.mocked(useModelVersionForJob);

function renderWithRouter(jobId: string) {
  return render(
    <MemoryRouter>
      <TrainedModelCard jobId={jobId} />
    </MemoryRouter>,
  );
}

describe("TrainedModelCard", () => {
  it("renders a Loading… placeholder while the query is in flight", () => {
    mocked.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useModelVersionForJob>);
    renderWithRouter("job-1");
    expect(screen.getByText(/Loading/)).toBeInTheDocument();
    // Card title is rendered in every state — the user always sees
    // a labelled tile while the data arrives.
    expect(screen.getByText("Trained model")).toBeInTheDocument();
  });

  it("renders the not-registered hint when the hook resolves to null", () => {
    mocked.mockReturnValue({
      data: null,
      isLoading: false,
    } as unknown as ReturnType<typeof useModelVersionForJob>);
    renderWithRouter("job-1");
    expect(screen.getByText(/Model not yet registered/)).toBeInTheDocument();
    // No registry link should render in this state.
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders the registry link + stage when the ModelVersion is loaded", () => {
    mocked.mockReturnValue({
      data: {
        owner: "alice",
        name: "upxelfdet",
        mlflow_version: 3,
        current_stage: "Production",
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useModelVersionForJob>);
    renderWithRouter("job-1");
    const link = screen.getByRole("link", { name: /alice\/upxelfdet v3/ });
    expect(link).toHaveAttribute("href", "/models/alice/upxelfdet");
    expect(screen.getByText(/Production/)).toBeInTheDocument();
  });
});
