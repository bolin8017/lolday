import { Link } from "react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useJobs,
  usePatchJob,
  type JobSummary,
  type JobType,
  type JobStatus,
} from "@/api/queries/jobs";
import { useAuth } from "@/hooks/useAuth";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/layout/PageHeader";
import { StatusBadge } from "@/components/common/StatusBadge";
import { FinalMetricsTile } from "@/components/jobs/FinalMetricsTile";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { PriorityToggle } from "@/components/forms/PriorityToggle";
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

/** Statuses where priority is no longer actionable and we show "—" instead. */
const TERMINAL_OR_RUNNING_STATUSES = [
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "timeout",
] as const satisfies readonly JobStatus[];

/** Badge + Popover priority cell — only rendered for admin users. */
function PriorityCell({ job }: { job: JobSummary }) {
  const { t } = useTranslation();
  const patch = usePatchJob();
  const canEdit = job.status === "queued_backend";
  const current: 0 | 1 = (job.priority ?? 0) === 0 ? 0 : 1;

  if (!canEdit) {
    if (
      (TERMINAL_OR_RUNNING_STATUSES as readonly string[]).includes(job.status)
    ) {
      return <span className="text-muted-foreground text-xs">—</span>;
    }
    return <PriorityBadge value={current} t={t} />;
  }

  function onChange(next: 0 | 1) {
    if (next === current) return;
    patch.mutate({ id: job.id, priority: next });
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t("jobs.priority.label")}
          className="cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-full"
        >
          <PriorityBadge value={current} t={t} />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-2" align="start">
        <PriorityToggle
          value={current}
          onChange={onChange}
          disabled={patch.isPending}
          size="sm"
        />
      </PopoverContent>
    </Popover>
  );
}

function PriorityBadge({
  value,
  t,
}: {
  value: 0 | 1;
  t: (k: string) => string;
}) {
  if (value === 1) {
    return (
      <Badge
        variant="outline"
        className="bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-300"
      >
        ⚡ {t("jobs.priority.high")}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground">
      {t("jobs.priority.normal")}
    </Badge>
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
