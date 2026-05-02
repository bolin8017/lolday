import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";

import RunRedirectPage from "@/routes/_authed.runs.$expId.$runId";

vi.mock("@/api/queries/runs", () => ({
  useRun: vi.fn(),
}));

const wrap = (initial: string) => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/runs/:expId/:runId" element={<RunRedirectPage />} />
          <Route path="/jobs/:id" element={<div data-testid="jobs-page" />} />
          <Route path="/runs" element={<div data-testid="runs-index" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("RunRedirectPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redirects to /jobs/<id> when run has lolday.job_id tag", async () => {
    const { useRun } = await import("@/api/queries/runs");
    (useRun as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { tags: { "lolday.job_id": "abc-123" } },
      isLoading: false,
      error: null,
    });
    render(wrap("/runs/exp1/run1"));
    await waitFor(() => screen.getByTestId("jobs-page"));
  });

  it("redirects to runs index on error", async () => {
    const { useRun } = await import("@/api/queries/runs");
    (useRun as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: null,
      isLoading: false,
      error: new Error("404"),
    });
    render(wrap("/runs/exp1/run1"));
    await waitFor(() => screen.getByTestId("runs-index"));
  });

  it("shows loading state while fetching", async () => {
    const { useRun } = await import("@/api/queries/runs");
    (useRun as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: null,
      isLoading: true,
      error: null,
    });
    render(wrap("/runs/exp1/run1"));
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("accepts the legacy lolday_job_id tag spelling", async () => {
    const { useRun } = await import("@/api/queries/runs");
    (useRun as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { tags: { lolday_job_id: "legacy-tag" } },
      isLoading: false,
      error: null,
    });
    render(wrap("/runs/exp1/run1"));
    await waitFor(() => screen.getByTestId("jobs-page"));
  });
});
