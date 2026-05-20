import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { ColumnDef } from "@tanstack/react-table";

type JobSummary = {
  id: string;
  type: "train" | "evaluate" | "predict";
  status:
    | "queued_backend"
    | "running"
    | "succeeded"
    | "failed"
    | "cancelled"
    | "timeout"
    | "preparing";
  detector_version_id: string;
  owner_id: string;
  mlflow_run_id: string | null;
  k8s_job_name: string | null;
  priority?: number | null;
  submitted_at: string;
  started_at: string | null;
  finished_at: string | null;
  summary_metrics?: Record<string, number> | null;
};

const { authState, queryState, navigateMock, capturedColumns, lastParams } =
  vi.hoisted(() => ({
    authState: {
      currentUser: { id: "u-self", role: "user" } as {
        id: string;
        role: "user" | "developer" | "admin";
      } | null,
    },
    queryState: {
      data: undefined as { items: JobSummary[] } | undefined,
      isLoading: false,
    },
    navigateMock: vi.fn(),
    capturedColumns: { current: undefined as unknown[] | undefined },
    lastParams: { current: undefined as Record<string, unknown> | undefined },
  }));

vi.mock("react-router", async () => {
  const actual =
    await vi.importActual<typeof import("react-router")>("react-router");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (k: string) => k,
  }),
}));

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({ currentUser: authState.currentUser }),
}));

vi.mock("@/api/queries/jobs", () => ({
  useJobs: (params: Record<string, unknown>) => {
    lastParams.current = params;
    return {
      data: queryState.data,
      isLoading: queryState.isLoading,
    };
  },
  // PriorityCell calls usePatchJob even when the page renders no rows;
  // stub so the import doesn't pull in a real TanStack mutation factory.
  usePatchJob: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("@/components/tables/DataTable", () => ({
  DataTable: <TRow,>({
    data,
    columns,
    emptyMessage,
    onRowClick,
  }: {
    data: TRow[];
    columns: ColumnDef<TRow>[];
    emptyMessage: string;
    onRowClick?: (row: TRow) => void;
  }) => {
    // Capture columns array so the admin-conditional Priority column
    // assertion below can introspect the rendered set without
    // re-rendering the real DataTable + RolePopover machinery.
    capturedColumns.current = columns;
    return (
      <div data-testid="stub-data-table">
        <span data-testid="stub-row-count">{data.length}</span>
        <span data-testid="stub-empty-message">{emptyMessage}</span>
        <span data-testid="stub-column-count">{columns.length}</span>
        {data.map((row, i) => (
          <button
            key={i}
            type="button"
            data-testid={`stub-row-${i}`}
            onClick={() => onRowClick?.(row)}
          >
            row-{i}
          </button>
        ))}
      </div>
    );
  },
}));

vi.mock("@/components/layout/PageHeader", () => ({
  PageHeader: ({
    title,
    actions,
  }: {
    title: string;
    actions?: React.ReactNode;
  }) => (
    <header>
      <h1>{title}</h1>
      <div data-testid="stub-header-actions">{actions}</div>
    </header>
  ),
}));

import JobsListPage, {
  handle as jobsHandle,
} from "@/routes/_authed.jobs._index";

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-x",
    type: "train",
    status: "queued_backend",
    detector_version_id: "dv-1",
    owner_id: "u-self",
    mlflow_run_id: null,
    k8s_job_name: null,
    priority: 0,
    submitted_at: "2026-05-19T00:00:00Z",
    started_at: null,
    finished_at: null,
    summary_metrics: null,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <JobsListPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  authState.currentUser = { id: "u-self", role: "user" };
  queryState.data = undefined;
  queryState.isLoading = false;
  navigateMock.mockReset();
  capturedColumns.current = undefined;
  lastParams.current = undefined;
});

describe("_authed.jobs._index.tsx (JobsListPage)", () => {
  it("renders Loading… while the query is in flight", () => {
    queryState.isLoading = true;
    const { getByText, queryByTestId } = renderPage();
    expect(getByText(/Loading…/)).toBeInTheDocument();
    expect(queryByTestId("stub-data-table")).toBeNull();
  });

  it("defaults the type filter to 'all' and sends an empty params object", () => {
    queryState.data = { items: [] };
    renderPage();
    // 'all' is shaped as {} so the backend returns every type; pin so
    // a refactor doesn't silently scope to type=train (and hide
    // evaluate / predict jobs).
    expect(lastParams.current).toEqual({});
  });

  it("renders the Submit-job CTA link pointing at /jobs/new", () => {
    queryState.data = { items: [] };
    const { getByRole } = renderPage();
    const submit = getByRole("link", { name: /Submit job/i });
    expect(submit).toHaveAttribute("href", "/jobs/new");
  });

  it("renders the type-filter trigger with a11y label", () => {
    queryState.data = { items: [] };
    const { getByLabelText } = renderPage();
    // aria-label needed because SelectValue isn't credited as the
    // button name by axe button-name (the comment on the source line
    // pins this contract).
    expect(getByLabelText("Filter by job type")).toBeInTheDocument();
  });

  it("passes the empty-message string through to DataTable when the list is empty", () => {
    queryState.data = { items: [] };
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("0");
    expect(getByTestId("stub-empty-message")).toHaveTextContent(
      /No jobs yet\./,
    );
  });

  it("tolerates undefined query.data via the `?.items ?? []` guard", () => {
    queryState.isLoading = false;
    queryState.data = undefined;
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("0");
  });

  it("renders one row per job item when the query has data", () => {
    queryState.data = {
      items: [
        makeJob({ id: "j-1", type: "train" }),
        makeJob({ id: "j-2", type: "evaluate" }),
      ],
    };
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("2");
  });

  it("navigates to /jobs/:id when a row is clicked", async () => {
    queryState.data = { items: [makeJob({ id: "j-42" })] };
    const userEvent = (await import("@testing-library/user-event")).default;
    const { getByTestId } = renderPage();
    await userEvent.setup().click(getByTestId("stub-row-0"));
    expect(navigateMock).toHaveBeenCalledWith("/jobs/j-42");
  });

  it("renders 5 columns for non-admin users (no Priority column)", () => {
    authState.currentUser = { id: "u-self", role: "user" };
    queryState.data = { items: [] };
    const { getByTestId } = renderPage();
    // baseColumns = type / status / submitted_at / duration / final_metrics
    expect(getByTestId("stub-column-count")).toHaveTextContent("5");
    expect(
      capturedColumns.current?.some(
        (c) => (c as { id?: string }).id === "priority",
      ),
    ).toBe(false);
  });

  it("renders 6 columns for admin users (Priority column appended)", () => {
    // Phase 6 (Task G.3) admin-conditional column. Without this gate
    // non-admins would see a column they can't act on.
    authState.currentUser = { id: "u-self", role: "admin" };
    queryState.data = { items: [] };
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-column-count")).toHaveTextContent("6");
    expect(
      capturedColumns.current?.some(
        (c) => (c as { id?: string }).id === "priority",
      ),
    ).toBe(true);
  });

  it("renders 5 columns for developer users (admin-only Priority column hidden)", () => {
    // Pin the asymmetry — only role==='admin' adds the column. A
    // refactor that broadened the gate to role!=='user' would let
    // developers see (but not act on) priority.
    authState.currentUser = { id: "u-self", role: "developer" };
    queryState.data = { items: [] };
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-column-count")).toHaveTextContent("5");
  });

  it("renders 5 columns when currentUser is null (treated as non-admin)", () => {
    authState.currentUser = null;
    queryState.data = { items: [] };
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-column-count")).toHaveTextContent("5");
  });

  it("exports the 'Jobs' breadcrumb handle", () => {
    expect(jobsHandle).toEqual({ breadcrumb: "Jobs" });
  });
});
