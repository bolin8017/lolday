import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { SourceModelCard } from "@/components/jobs/SourceModelCard";

/**
 * `SourceModelCard` is the tile on Evaluate / Predict job-detail pages
 * that shows the ModelVersion the job is scored against. Four user-
 * visible states to pin:
 *
 * - Loading -> "Loading…" placeholder, labelled title visible.
 * - Error or `data === null` -> the "Failed to load source model" line.
 *   The two share a branch in the component (`error || !data`) so we
 *   exercise both inputs.
 * - Data present -> registry link to `/models/<owner>/<name>` +
 *   "v<n> (<stage>)" line.
 * - When the ModelVersion carries a `source_job_id`, an extra line
 *   shows "Trained by: job <8-hex>" linking to `/jobs/<full-id>`.
 *   The slice(0,8) is a UX call (long IDs blow out the card width);
 *   pin it so a future refactor doesn't either expand to the full id
 *   or drop the slice entirely.
 */

vi.mock("@/api/queries/models", () => ({
  useModelVersion: vi.fn(),
}));

import { useModelVersion } from "@/api/queries/models";

const mocked = vi.mocked(useModelVersion);

function renderWithRouter() {
  return render(
    <MemoryRouter>
      <SourceModelCard sourceModelVersionId="mv-1" />
    </MemoryRouter>,
  );
}

describe("SourceModelCard", () => {
  it("renders a Loading… placeholder while the query is in flight", () => {
    mocked.mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as unknown as ReturnType<typeof useModelVersion>);
    renderWithRouter();
    expect(screen.getByText("Source model")).toBeInTheDocument();
    expect(screen.getByText(/Loading/)).toBeInTheDocument();
  });

  it("renders the failed-to-load hint when the hook errors", () => {
    mocked.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("network"),
    } as unknown as ReturnType<typeof useModelVersion>);
    renderWithRouter();
    expect(screen.getByText(/Failed to load source model/)).toBeInTheDocument();
  });

  it("renders the failed-to-load hint when data is null without an error", () => {
    mocked.mockReturnValue({
      data: null,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useModelVersion>);
    renderWithRouter();
    expect(screen.getByText(/Failed to load source model/)).toBeInTheDocument();
  });

  it("renders the registry link + version/stage line when data is present", () => {
    mocked.mockReturnValue({
      data: {
        owner: "alice",
        name: "upxelfdet",
        mlflow_version: 2,
        current_stage: "Production",
        source_job_id: null,
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useModelVersion>);
    renderWithRouter();
    const modelLink = screen.getByRole("link", { name: "alice/upxelfdet" });
    expect(modelLink).toHaveAttribute("href", "/models/alice/upxelfdet");
    expect(screen.getByText(/v2 \(Production\)/)).toBeInTheDocument();
    // Without source_job_id, the "Trained by" line is suppressed.
    expect(screen.queryByText(/Trained by/)).not.toBeInTheDocument();
  });

  it("shows the trained-by link, slicing the job id to 8 hex chars", () => {
    mocked.mockReturnValue({
      data: {
        owner: "alice",
        name: "upxelfdet",
        mlflow_version: 5,
        current_stage: "Staging",
        source_job_id: "abcdef1234567890",
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useModelVersion>);
    renderWithRouter();
    const jobLink = screen.getByRole("link", { name: "job abcdef12" });
    // Link target uses the FULL job id even though the label is sliced.
    expect(jobLink).toHaveAttribute("href", "/jobs/abcdef1234567890");
  });
});
