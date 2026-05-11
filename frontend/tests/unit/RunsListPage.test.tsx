import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/queries/runs", () => ({
  useExperimentRuns: () => ({
    data: [
      {
        run_id: "lolday-run-id-123456",
        run_name: "lolday-run",
        status: "FINISHED",
        start_time: 0,
        end_time: 0,
        tags: { "lolday.job_id": "job-A" },
        lolday_started_at: "2026-05-11T10:05:00+00:00",
        lolday_finished_at: "2026-05-11T10:15:00+00:00",
      },
      {
        run_id: "orphan-run-id-654321",
        run_name: "orphan-run",
        status: "FINISHED",
        start_time: 0,
        end_time: 0,
        tags: {},
        lolday_started_at: null,
        lolday_finished_at: null,
      },
    ],
    isLoading: false,
  }),
}));

import RunsListPage from "@/routes/_authed.runs.$expId";

const wrap = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/runs/exp1"]}>
        <Routes>
          <Route path="/runs/:expId" element={<RunsListPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe("RunsListPage cell linking", () => {
  it("renders an internal Link to /jobs/<id> when row has lolday.job_id tag", () => {
    wrap();
    // Find the anchor whose text starts with the run_id slice "lolday-run"
    // (the Run cell renders it inside <a>, while the Name cell is plain text).
    const matches = screen.getAllByText(/^lolday-run/i);
    const link = matches.find((el) => el.closest("a")) as HTMLElement;
    const a = link.closest("a")!;
    expect(a.getAttribute("href")).toBe("/jobs/job-A");
  });

  it("renders external link to MLflow when row has no lolday.job_id tag", () => {
    wrap();
    const matches = screen.getAllByText(/^orphan-run/i);
    const link = matches.find((el) => el.closest("a")) as HTMLElement;
    const a = link.closest("a")!;
    expect(a.getAttribute("href")).toContain(
      "/mlflow/#/experiments/exp1/runs/orphan-run-id-654321",
    );
    expect(a.getAttribute("target")).toBe("_blank");
  });

  it("does not render a separate Job column", () => {
    wrap();
    expect(screen.queryByRole("columnheader", { name: /^job$/i })).toBeNull();
  });

  it("renders Compute time from lolday timestamps when present", () => {
    wrap();
    // 10:15:00 - 10:05:00 = 10 minutes
    expect(screen.getByText(/10m\b/i)).toBeInTheDocument();
  });

  it("renders em dash when lolday timestamps are null (orphan run)", () => {
    wrap();
    // orphan row has both timestamps null; the duration cell renders em dash.
    // There are no other em-dash placeholders in this fixture, so finding any
    // proves the fall-through behaves.
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("column header is 'Compute time' not 'Duration'", () => {
    wrap();
    expect(
      screen.getByRole("columnheader", { name: /compute time/i }),
    ).toBeInTheDocument();
  });
});
