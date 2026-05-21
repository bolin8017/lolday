import { render, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { ColumnDef } from "@tanstack/react-table";

type Row = {
  run_id: string;
  run_name?: string;
  status: string;
  metrics?: Record<string, number>;
  params?: Record<string, string>;
  tags?: Record<string, string>;
  lolday_started_at?: string | null;
  lolday_finished_at?: string | null;
};

const { queryState, capturedTableProps, capturedColumnPickerProps } =
  vi.hoisted(() => ({
    queryState: {
      data: undefined as Row[] | undefined,
      isLoading: false,
    },
    capturedTableProps: {
      rows: undefined as Row[] | undefined,
      emptyMessage: undefined as string | undefined,
      columnIds: undefined as (string | undefined)[] | undefined,
      // Expose the full columns array so tests can invoke specific
      // ColumnDef.cell callbacks against synthetic rows. Used to pin
      // the pickValue branches + cell-rendering fallbacks (#530-era
      // route-shell coverage gap).
      columns: undefined as
        | {
            id?: string;
            accessorKey?: string;
            cell?: (ctx: { row: { original: Row } }) => unknown;
          }[]
        | undefined,
    },
    capturedColumnPickerProps: {
      availableMetrics: undefined as string[] | undefined,
      availableParams: undefined as string[] | undefined,
      selected: undefined as string[] | undefined,
    },
  }));

vi.mock("@/api/queries/runs", () => ({
  useExperimentRuns: () => ({
    data: queryState.data,
    isLoading: queryState.isLoading,
  }),
}));

// Capture the props passed to DataTable so we can introspect filter +
// column construction without re-rendering the real table machinery.
vi.mock("@/components/tables/DataTable", () => ({
  DataTable: <TRow,>({
    data,
    columns,
    emptyMessage,
  }: {
    data: TRow[];
    columns: ColumnDef<TRow>[];
    emptyMessage: string;
  }) => {
    capturedTableProps.rows = data as unknown as Row[];
    capturedTableProps.emptyMessage = emptyMessage;
    capturedTableProps.columnIds = columns.map(
      (c) =>
        (c as { id?: string; accessorKey?: string }).id ??
        (c as { accessorKey?: string }).accessorKey,
    );
    capturedTableProps.columns =
      columns as unknown as typeof capturedTableProps.columns;
    return (
      <div data-testid="stub-data-table">
        <span data-testid="stub-row-count">{data.length}</span>
      </div>
    );
  },
}));

// RunsColumnPicker has its own dedicated suite; capture only the
// availableMetrics / availableParams / selected props this route
// computes for it (key-discovery + per-expId localStorage hydration).
vi.mock("@/components/runs/RunsColumnPicker", async () => {
  const actual = await vi.importActual<
    typeof import("@/components/runs/RunsColumnPicker")
  >("@/components/runs/RunsColumnPicker");
  return {
    ...actual,
    RunsColumnPicker: (props: {
      availableMetrics: string[];
      availableParams: string[];
      selected: string[];
    }) => {
      capturedColumnPickerProps.availableMetrics = props.availableMetrics;
      capturedColumnPickerProps.availableParams = props.availableParams;
      capturedColumnPickerProps.selected = props.selected;
      return <div data-testid="stub-column-picker" />;
    },
  };
});

// RunsStatusFilter has its own dedicated suite. Keep the real
// isRunsStatus type guard + RUNS_STATUSES exports so the route's
// localStorage rehydration logic still works.
vi.mock("@/components/runs/RunsStatusFilter", async () => {
  const actual = await vi.importActual<
    typeof import("@/components/runs/RunsStatusFilter")
  >("@/components/runs/RunsStatusFilter");
  return {
    ...actual,
    RunsStatusFilter: ({
      value,
      onChange,
    }: {
      value: string;
      onChange: (s: string) => void;
    }) => (
      <button
        type="button"
        data-testid="stub-status-filter"
        data-value={value}
        onClick={() => onChange("RUNNING")}
      >
        status-filter
      </button>
    ),
  };
});

vi.mock("@/components/common/OpenInMlflowButton", () => ({
  OpenInMlflowButton: () => <button data-testid="stub-open-mlflow" />,
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

import RunsListPage, {
  handle as runsHandle,
} from "@/routes/_authed.runs.$expId";

function renderAt(expId = "exp-1") {
  return render(
    <MemoryRouter initialEntries={[`/runs/${expId}`]}>
      <Routes>
        <Route path="/runs/:expId" element={<RunsListPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  queryState.data = undefined;
  queryState.isLoading = false;
  capturedTableProps.rows = undefined;
  capturedTableProps.emptyMessage = undefined;
  capturedTableProps.columnIds = undefined;
  capturedTableProps.columns = undefined;
  capturedColumnPickerProps.availableMetrics = undefined;
  capturedColumnPickerProps.availableParams = undefined;
  capturedColumnPickerProps.selected = undefined;
  localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("_authed.runs.\\$expId.tsx (RunsListPage)", () => {
  it("renders Loading… while the query is in flight", () => {
    queryState.isLoading = true;
    const { getByText, queryByTestId } = renderAt();
    expect(getByText(/Loading…/)).toBeInTheDocument();
    expect(queryByTestId("stub-data-table")).toBeNull();
  });

  it("defaults the status filter to 'all' when no value is stored", () => {
    queryState.data = [];
    const { getByTestId } = renderAt();
    expect(getByTestId("stub-status-filter")).toHaveAttribute(
      "data-value",
      "all",
    );
  });

  it("rehydrates the status filter from the per-expId localStorage key", () => {
    // Per-expId key: `lolday.runs.status.${expId}` — pin so a future
    // refactor doesn't drop the per-experiment scoping (a global key
    // would leak state across experiments).
    localStorage.setItem("lolday.runs.status.exp-99", "FAILED");
    queryState.data = [];
    const { getByTestId } = renderAt("exp-99");
    expect(getByTestId("stub-status-filter")).toHaveAttribute(
      "data-value",
      "FAILED",
    );
  });

  it("ignores a malformed stored status value (isRunsStatus guard)", () => {
    // Defense-in-depth: a tampered localStorage value (e.g. "evil") is
    // dropped by the isRunsStatus type guard and the default 'all' wins.
    localStorage.setItem("lolday.runs.status.exp-1", "evil");
    queryState.data = [];
    const { getByTestId } = renderAt();
    expect(getByTestId("stub-status-filter")).toHaveAttribute(
      "data-value",
      "all",
    );
  });

  it("filters rows by status (case-insensitive match against RunsStatus uppercase)", () => {
    // Source: `r.status.toUpperCase() === status` — pin so a refactor
    // doesn't accidentally make the comparison case-sensitive (real
    // MLflow runs ship status uppercase, but the guard tolerates mixed
    // case in case the backend ever lowercases).
    localStorage.setItem("lolday.runs.status.exp-1", "FAILED");
    queryState.data = [
      { run_id: "r-1", status: "FAILED" },
      { run_id: "r-2", status: "finished" }, // lower; would not match
      { run_id: "r-3", status: "Failed" }, // mixed case
    ];
    renderAt();
    expect(capturedTableProps.rows?.map((r) => r.run_id)).toEqual([
      "r-1",
      "r-3",
    ]);
  });

  it("shows every row when status='all'", () => {
    queryState.data = [
      { run_id: "r-1", status: "FINISHED" },
      { run_id: "r-2", status: "FAILED" },
    ];
    renderAt();
    expect(capturedTableProps.rows).toHaveLength(2);
  });

  it("passes the empty-message string through to DataTable", () => {
    queryState.data = [];
    renderAt();
    expect(capturedTableProps.emptyMessage).toBe("No runs match the filter.");
  });

  it("derives availableMetrics / availableParams from the row set (sorted)", () => {
    queryState.data = [
      {
        run_id: "r-1",
        status: "FINISHED",
        metrics: { f1: 0.9, recall: 0.85 },
        params: { lr: "0.01" },
      },
      {
        run_id: "r-2",
        status: "FINISHED",
        metrics: { f1: 0.7, precision: 0.8 },
        params: { batch_size: "32" },
      },
    ];
    renderAt();
    expect(capturedColumnPickerProps.availableMetrics).toEqual([
      "f1",
      "precision",
      "recall",
    ]);
    expect(capturedColumnPickerProps.availableParams).toEqual([
      "batch_size",
      "lr",
    ]);
  });

  it("seeds selectedCols from DEFAULT_COLS when localStorage has no entry", () => {
    queryState.data = [];
    renderAt();
    // DEFAULT_COLS = ['metrics.f1', 'metrics.accuracy']. Pin so a
    // refactor doesn't silently drop the default visibility for
    // common training metrics.
    expect(capturedColumnPickerProps.selected).toEqual([
      "metrics.f1",
      "metrics.accuracy",
    ]);
  });

  it("appends selectedCols as extra columns onto the 4 base columns", () => {
    queryState.data = [
      {
        run_id: "r-1",
        status: "FINISHED",
        metrics: { f1: 0.9 },
      },
    ];
    renderAt();
    // Base = run_id / run_name / status / duration. Default selected
    // cols = metrics.f1 + metrics.accuracy → 6 columns total.
    expect(capturedTableProps.columnIds).toEqual([
      "run_id",
      "run_name",
      "status",
      "duration",
      "metrics.f1",
      "metrics.accuracy",
    ]);
  });

  it("exports the 'Experiment' breadcrumb handle", () => {
    expect(runsHandle).toEqual({ breadcrumb: "Experiment" });
  });

  // pickValue branches + extra-column cell renderer fallbacks (#530 era
  // route-shell coverage gap, lines 42-44 + 150 of _authed.runs.\$expId.tsx).
  it("extra-column metrics cell renders numeric metric formatted to 4 decimals", () => {
    localStorage.setItem(
      "lolday.runs.columns.exp-1",
      JSON.stringify(["metrics.f1"]),
    );
    queryState.data = [
      {
        run_id: "r-1",
        status: "FINISHED",
        metrics: { f1: 0.123456789 },
      },
    ];
    renderAt();
    const col = capturedTableProps.columns!.find((c) => c.id === "metrics.f1");
    expect(col).toBeDefined();
    const out = col!.cell!({
      row: { original: queryState.data![0] },
    });
    expect(out).toBe("0.1235");
  });

  it("extra-column params cell renders the param string via pickValue('params', ...)", () => {
    localStorage.setItem(
      "lolday.runs.columns.exp-1",
      JSON.stringify(["params.lr"]),
    );
    queryState.data = [
      {
        run_id: "r-1",
        status: "FINISHED",
        // params are strings on the backend; pickValue's params branch
        // (L42) returns the raw string and the cell renderer falls
        // through to String(v) (L150).
        params: { lr: "0.001" },
      },
    ];
    renderAt();
    const col = capturedTableProps.columns!.find((c) => c.id === "params.lr");
    expect(col).toBeDefined();
    const out = col!.cell!({
      row: { original: queryState.data![0] },
    });
    expect(out).toBe("0.001");
  });

  it("extra-column tags cell renders the tag string via pickValue('tags', ...)", () => {
    localStorage.setItem(
      "lolday.runs.columns.exp-1",
      JSON.stringify(["tags.maldet_version"]),
    );
    queryState.data = [
      {
        run_id: "r-1",
        status: "FINISHED",
        // Tags branch (L43) — string output flows through L150 fallback.
        tags: { maldet_version: "2.2.1" },
      },
    ];
    renderAt();
    const col = capturedTableProps.columns!.find(
      (c) => c.id === "tags.maldet_version",
    );
    expect(col).toBeDefined();
    const out = col!.cell!({
      row: { original: queryState.data![0] },
    });
    expect(out).toBe("2.2.1");
  });

  it("extra-column cell renders em-dash when the looked-up value is missing", () => {
    localStorage.setItem(
      "lolday.runs.columns.exp-1",
      JSON.stringify(["metrics.f1"]),
    );
    queryState.data = [
      {
        run_id: "r-1",
        status: "FINISHED",
        // No metrics key → pickValue('metrics', 'f1') returns undefined →
        // cell renderer hits the `v == null` branch and prints "—".
        metrics: {},
      },
    ];
    renderAt();
    const col = capturedTableProps.columns!.find((c) => c.id === "metrics.f1");
    const out = col!.cell!({
      row: { original: queryState.data![0] },
    });
    expect(out).toBe("—");
  });
});
