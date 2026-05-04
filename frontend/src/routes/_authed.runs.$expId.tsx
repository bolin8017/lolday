import { Link, useParams } from "react-router";
import { useState, useMemo, useEffect } from "react";
import { useExperimentRuns } from "@/api/queries/runs";
import { DataTable } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/common/StatusBadge";
import {
  RunsColumnPicker,
  loadColumnsFromStorage,
} from "@/components/runs/RunsColumnPicker";
import {
  RunsStatusFilter,
  isRunsStatus,
  type RunsStatus,
} from "@/components/runs/RunsStatusFilter";
import { OpenInMlflowButton } from "@/components/common/OpenInMlflowButton";
import { PageHeader } from "@/components/layout/PageHeader";
import { formatDuration } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Experiment" };

interface Row {
  run_id: string;
  run_name?: string;
  status: string;
  start_time?: number;
  end_time?: number;
  metrics?: Record<string, number>;
  params?: Record<string, string>;
  tags?: Record<string, string>;
}

const DEFAULT_COLS = ["metrics.f1", "metrics.accuracy"];

function pickValue(row: Row, kind: string, name: string): unknown {
  if (kind === "metrics") return row.metrics?.[name];
  if (kind === "params") return row.params?.[name];
  if (kind === "tags") return row.tags?.[name];
  return undefined;
}

export default function RunsListPage() {
  const { expId = "" } = useParams();
  const { data, isLoading } = useExperimentRuns(expId);
  const rows: Row[] = useMemo(() => data ?? [], [data]);

  // Discover available metric/param keys from the data.
  const { availableMetrics, availableParams } = useMemo(() => {
    const m = new Set<string>();
    const p = new Set<string>();
    for (const r of rows) {
      Object.keys(r.metrics ?? {}).forEach((k) => m.add(k));
      Object.keys(r.params ?? {}).forEach((k) => p.add(k));
    }
    return {
      availableMetrics: Array.from(m).sort(),
      availableParams: Array.from(p).sort(),
    };
  }, [rows]);

  const [selectedCols, setSelectedCols] = useState<string[]>(() =>
    loadColumnsFromStorage(expId, DEFAULT_COLS),
  );
  const [status, setStatus] = useState<RunsStatus>(() => {
    const v = localStorage.getItem(`runs.status.${expId}`);
    return isRunsStatus(v) ? v : "all";
  });
  useEffect(() => {
    localStorage.setItem(`runs.status.${expId}`, status);
  }, [expId, status]);

  // Filter rows by status
  const filteredRows =
    status === "all"
      ? rows
      : rows.filter((r) => r.status.toUpperCase() === status);

  // Build columns
  const columns: ColumnDef<Row>[] = [
    {
      accessorKey: "run_id",
      header: "Run",
      cell: ({ row }) => {
        const jobId =
          row.original.tags?.["lolday.job_id"] ??
          row.original.tags?.lolday_job_id;
        if (jobId) {
          return (
            <Link
              to={`/jobs/${jobId}`}
              className="font-mono text-sm hover:underline"
            >
              {row.original.run_id.slice(0, 10)}
            </Link>
          );
        }
        return (
          <a
            href={`/mlflow/#/experiments/${expId}/runs/${row.original.run_id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-sm hover:underline"
          >
            {row.original.run_id.slice(0, 10)} ↗
          </a>
        );
      },
      meta: { cardSlot: "title" },
    },
    {
      accessorKey: "run_name",
      header: "Name",
      meta: { cardLabel: "Name", cardSlot: "body" },
    },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => (
        <StatusBadge status={row.original.status.toLowerCase()} />
      ),
      meta: { cardSlot: "subtitle" },
    },
    {
      id: "duration",
      header: "Duration",
      cell: ({ row }) =>
        row.original.start_time && row.original.end_time
          ? formatDuration(
              new Date(row.original.start_time).toISOString(),
              new Date(row.original.end_time).toISOString(),
            )
          : "—",
      meta: { cardLabel: "Duration", cardSlot: "body" },
    },
    ...selectedCols.map((key): ColumnDef<Row> => {
      const [kind, name] = key.split(".", 2);
      return {
        id: key,
        header: name,
        cell: ({ row }) => {
          const v = pickValue(row.original, kind, name);
          if (typeof v === "number") return v.toFixed(4);
          if (v == null) return "—";
          return String(v);
        },
        meta: { cardLabel: name, cardSlot: "body" },
      };
    }),
  ];

  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Runs"
        actions={
          <>
            <RunsStatusFilter value={status} onChange={setStatus} />
            <RunsColumnPicker
              experimentId={expId}
              availableMetrics={availableMetrics}
              availableParams={availableParams}
              selected={selectedCols}
              onChange={setSelectedCols}
            />
            <OpenInMlflowButton experimentId={expId} />
          </>
        }
      />
      <DataTable
        data={filteredRows}
        columns={columns}
        emptyMessage="No runs match the filter."
      />
    </div>
  );
}
