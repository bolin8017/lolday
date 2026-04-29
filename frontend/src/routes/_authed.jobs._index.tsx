import { Link } from "react-router";
import { useState } from "react";
import { useJobs, type JobSummary, type JobType } from "@/api/queries/jobs";
import { DataTable } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/common/StatusBadge";
import { FinalMetricsTile } from "@/components/jobs/FinalMetricsTile";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatRelative, formatDuration } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";

export const handle = { breadcrumb: "Jobs" };

const columns: ColumnDef<JobSummary>[] = [
  {
    accessorKey: "type",
    header: "Type",
    cell: ({ row }) => <Badge variant="outline">{row.original.type}</Badge>,
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
  },
  {
    accessorKey: "submitted_at",
    header: "Submitted",
    cell: ({ row }) => formatRelative(row.original.submitted_at),
  },
  {
    id: "duration",
    header: "Duration",
    cell: ({ row }) =>
      formatDuration(row.original.started_at, row.original.finished_at),
  },
  {
    id: "final_metrics",
    header: "Final metrics",
    cell: ({ row }) => (
      <FinalMetricsTile summaryMetrics={row.original.summary_metrics} />
    ),
  },
];

export default function JobsListPage() {
  const [type, setType] = useState<JobType | "all">("all");
  const params = type === "all" ? {} : { type };
  const { data, isLoading } = useJobs(params);
  const rows: JobSummary[] = data?.items ?? [];
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Jobs</h1>
        <div className="flex items-center gap-2">
          <Select value={type} onValueChange={(v) => setType(v as typeof type)}>
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              <SelectItem value="train">Train</SelectItem>
              <SelectItem value="evaluate">Evaluate</SelectItem>
              <SelectItem value="predict">Predict</SelectItem>
            </SelectContent>
          </Select>
          <Button asChild>
            <Link to="/jobs/new">
              <Plus className="mr-2 h-4 w-4" />
              Submit job
            </Link>
          </Button>
        </div>
      </div>
      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : (
        <DataTable
          data={rows}
          columns={columns}
          emptyMessage="No jobs yet."
          onRowClick={(j) => {
            window.location.href = `/jobs/${j.id}`;
          }}
        />
      )}
    </div>
  );
}
