import { Link } from "react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useJobs,
  usePatchJob,
  type JobSummary,
  type JobType,
} from "@/api/queries/jobs";
import { useAuth } from "@/hooks/useAuth";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatusBadge } from "@/components/common/StatusBadge";
import { FinalMetricsTile } from "@/components/jobs/FinalMetricsTile";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
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

/** Inline-edit priority cell — only rendered for admin users. */
function PriorityCell({ job }: { job: JobSummary }) {
  const { t } = useTranslation();
  const patch = usePatchJob();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(job.priority ?? 0);

  const canEdit = job.status === "queued_backend";

  function commit() {
    if (draft !== (job.priority ?? 0)) {
      patch.mutate({ id: job.id, priority: draft });
    }
    setEditing(false);
  }

  if (canEdit && editing) {
    return (
      <Input
        type="number"
        min={0}
        step={1}
        className="h-7 w-16 px-1 text-sm"
        autoFocus
        value={draft}
        onChange={(e) => {
          const v = parseInt(e.target.value, 10);
          setDraft(isNaN(v) || v < 0 ? 0 : v);
        }}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setEditing(false);
        }}
        aria-label={t("jobs.priority.label")}
      />
    );
  }

  return (
    <span
      className={
        canEdit
          ? "cursor-pointer underline-offset-2 hover:underline"
          : undefined
      }
      title={canEdit ? t("jobs.priority.save") : undefined}
      onClick={canEdit ? () => setEditing(true) : undefined}
    >
      {job.priority ?? 0}
    </span>
  );
}

const baseColumns: ColumnDef<JobSummary>[] = [
  {
    accessorKey: "type",
    header: "Type",
    cell: ({ row }) => <Badge variant="outline">{row.original.type}</Badge>,
    meta: { cardSlot: "title" },
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
    meta: { cardSlot: "subtitle" },
  },
  {
    accessorKey: "submitted_at",
    header: "Submitted",
    cell: ({ row }) => formatRelative(row.original.submitted_at),
    meta: { cardLabel: "Submitted", cardSlot: "body" },
  },
  {
    id: "duration",
    header: "Duration",
    cell: ({ row }) =>
      formatDuration(row.original.started_at, row.original.finished_at),
    meta: { cardLabel: "Duration", cardSlot: "body" },
  },
  {
    id: "final_metrics",
    header: "Final metrics",
    cell: ({ row }) => (
      <FinalMetricsTile summaryMetrics={row.original.summary_metrics} />
    ),
    meta: { cardLabel: "Metrics", cardSlot: "body" },
  },
];

export default function JobsListPage() {
  const { t } = useTranslation();
  const { currentUser } = useAuth();
  const isAdmin = currentUser?.role === "admin";

  const [type, setType] = useState<JobType | "all">("all");
  const params = type === "all" ? {} : { type };
  const { data, isLoading } = useJobs(params);
  const rows: JobSummary[] = data?.items ?? [];

  // Phase 6 (Task G.3) — admin sees Priority column
  const columns: ColumnDef<JobSummary>[] = isAdmin
    ? [
        ...baseColumns,
        {
          id: "priority",
          header: t("jobs.priority.column"),
          cell: ({ row }) => <PriorityCell job={row.original} />,
          meta: { cardLabel: t("jobs.priority.column"), cardSlot: "body" },
        },
      ]
    : baseColumns;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Jobs"
        actions={
          <>
            <Select
              value={type}
              onValueChange={(v) => setType(v as typeof type)}
            >
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
          </>
        }
      />
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
