import { render } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

const { queryState } = vi.hoisted(() => ({
  queryState: {
    data: undefined as null | { tags?: Record<string, string> } | undefined,
    isLoading: false,
    error: null as unknown,
  },
}));

vi.mock("@/api/queries/runs", () => ({
  useRun: () => ({
    data: queryState.data,
    isLoading: queryState.isLoading,
    error: queryState.error,
  }),
}));

import RunRedirectPage from "@/routes/_authed.runs.$expId.$runId";

// jsdom marks `window.location.replace` as non-configurable, so
// `vi.spyOn` and per-property defineProperty both throw
// "Cannot redefine property". Mirror the
// `frontend/tests/unit/hooks/useAuth.test.ts` pattern: replace the
// whole `window.location` with a stub that captures replace() calls.
// MemoryRouter does not read window.location (it owns its own
// history), so dropping pathname / origin etc. is safe here.
let replaceMock: ReturnType<typeof vi.fn>;
let originalLocation: Location;

beforeEach(() => {
  queryState.data = undefined;
  queryState.isLoading = false;
  queryState.error = null;
  replaceMock = vi.fn();
  originalLocation = window.location;
  Object.defineProperty(window, "location", {
    value: { replace: replaceMock },
    writable: true,
    configurable: true,
  });
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    value: originalLocation,
    writable: true,
    configurable: true,
  });
});

function renderAt(path: string, sentinel = "destination") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/runs/:expId/:runId" element={<RunRedirectPage />} />
        <Route path="/runs" element={<div data-testid={sentinel}>runs</div>} />
        <Route
          path="/jobs/:id"
          element={<div data-testid={sentinel}>job-detail</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("_authed.runs.$expId.$runId.tsx (RunRedirectPage)", () => {
  it("renders Loading… while the run query is in flight", () => {
    queryState.isLoading = true;
    queryState.data = undefined;
    const { getByText, queryByTestId } = renderAt("/runs/exp-1/run-1");
    expect(getByText(/Loading…/)).toBeInTheDocument();
    // Loading branch must NOT trigger any of the redirect sentinels.
    expect(queryByTestId("destination")).toBeNull();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("Navigate-redirects to /runs when the query errors out", () => {
    queryState.isLoading = false;
    queryState.error = new Error("boom");
    queryState.data = undefined;
    const { getByTestId } = renderAt("/runs/exp-1/run-1");
    // <Navigate to="/runs" replace /> resolves to the /runs sentinel
    // route in MemoryRouter — pin so a refactor doesn't silently change
    // the fallback (which would orphan users on a blank page).
    expect(getByTestId("destination")).toHaveTextContent("runs");
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("Navigate-redirects to /runs when data is null + no error", () => {
    queryState.isLoading = false;
    queryState.error = null;
    queryState.data = null;
    const { getByTestId } = renderAt("/runs/exp-1/run-1");
    // Same fallback for the `error || !data` branch — pin the defensive
    // !data guard separately so a refactor doesn't silently lose it.
    expect(getByTestId("destination")).toHaveTextContent("runs");
  });

  it("Navigate-redirects to /jobs/:id when the run carries a lolday.job_id tag", () => {
    queryState.isLoading = false;
    queryState.data = {
      tags: { "lolday.job_id": "job-42" },
    };
    const { getByTestId } = renderAt("/runs/exp-1/run-1");
    expect(getByTestId("destination")).toHaveTextContent("job-detail");
    // The job-id branch must NOT fall through to the external MLflow
    // redirect — that path is reserved for orphan runs.
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("honours the legacy snake_case `lolday_job_id` tag too", () => {
    // Tag naming was historically `lolday_job_id`; the route's
    // `?? run?.tags?.lolday_job_id ?? null` fallback covers older runs.
    // Pin so a refactor doesn't drop the legacy alias.
    queryState.isLoading = false;
    queryState.data = {
      tags: { lolday_job_id: "job-legacy" },
    };
    const { getByTestId } = renderAt("/runs/exp-1/run-1");
    expect(getByTestId("destination")).toHaveTextContent("job-detail");
  });

  it("orphan run (no job-id tag) replaces window.location with the MLflow URL", () => {
    queryState.isLoading = false;
    queryState.data = { tags: {} };
    const { getByText } = renderAt("/runs/exp-A/run-B");
    // Visible UI is the "Redirecting to MLflow…" copy while the
    // useEffect-scheduled window.location.replace fires.
    expect(getByText(/Redirecting to MLflow…/)).toBeInTheDocument();
    expect(replaceMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith(
      "/mlflow/#/experiments/exp-A/runs/run-B",
    );
  });
});
