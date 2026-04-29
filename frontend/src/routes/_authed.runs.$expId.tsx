import { Link, useParams } from "react-router";
import { useExperimentRuns } from "@/api/queries/runs";
import { DataTable } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/common/StatusBadge";
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
  tags?: Record<string, string>;
}

export default function RunsListPage() {
  const { expId = "" } = useParams();
  const { data, isLoading } = useExperimentRuns(expId);

  const columns: ColumnDef<Row>[] = [
    {
      accessorKey: "run_id",
      header: "Run",
      cell: ({ row }) => (
        <Link
          to={`/runs/${expId}/${row.original.run_id}`}
          className="font-mono text-sm hover:underline"
        >
          {row.original.run_id.slice(0, 10)}
        </Link>
      ),
    },
    { accessorKey: "run_name", header: "Name" },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => (
        <StatusBadge status={row.original.status.toLowerCase()} />
      ),
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
    },
    {
      id: "accuracy",
      header: "Accuracy",
      cell: ({ row }) => row.original.metrics?.accuracy?.toFixed(4) ?? "—",
    },
    {
      id: "f1",
      header: "F1",
      cell: ({ row }) =>
        (row.original.metrics?.f1 ?? row.original.metrics?.f1_score)?.toFixed(
          4,
        ) ?? "—",
    },
    {
      id: "job",
      header: "Job",
      cell: ({ row }) => {
        const jobId =
          row.original.tags?.["lolday.job_id"] ??
          row.original.tags?.lolday_job_id;
        return jobId ? (
          <Link to={`/jobs/${jobId}`} className="text-primary hover:underline">
            ↗
          </Link>
        ) : (
          "—"
        );
      },
    },
  ];

  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Runs</h1>
      <DataTable
        data={data ?? []}
        columns={columns}
        emptyMessage="No runs yet."
      />
    </div>
  );
}
