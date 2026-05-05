import { ReactNode, useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "@/components/common/StatusBadge";
import { OpenInMlflowButton } from "@/components/common/OpenInMlflowButton";
import { useCancelJob, usePatchJob } from "@/api/queries/jobs";
import { useAuth } from "@/hooks/useAuth";
import { useJobQueuePosition } from "@/api/queries/cluster";
import { isTerminal } from "@/lib/status";
import { formatDuration, formatRelative } from "@/lib/date";
import type { components } from "@/api/schema.gen";

type JobRead = components["schemas"]["JobRead"];

/** Phase 6 (Task G.4 + G.5) — admin-only inline priority editor in job detail. */
function PriorityEditor({ job }: { job: JobRead }) {
  const { t } = useTranslation();
  const patch = usePatchJob();
  const [draft, setDraft] = useState(job.priority ?? 0);
  const [saved, setSaved] = useState(false);

  const canEdit = job.status === "queued_backend";

  function save() {
    if (draft === (job.priority ?? 0)) return;
    patch.mutate(
      { id: job.id, priority: draft },
      {
        onSuccess: () => setSaved(true),
      },
    );
  }

  if (!canEdit) {
    return <span>{job.priority ?? 0}</span>;
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Label htmlFor="detail-priority-input" className="sr-only">
          {t("jobs.priority.label")}
        </Label>
        <Input
          id="detail-priority-input"
          type="number"
          min={0}
          step={1}
          className="h-7 w-20 px-2 text-sm"
          value={draft}
          onChange={(e) => {
            const v = parseInt(e.target.value, 10);
            setDraft(isNaN(v) || v < 0 ? 0 : v);
            setSaved(false);
          }}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={patch.isPending || draft === (job.priority ?? 0)}
          onClick={save}
        >
          {patch.isPending ? "…" : t("jobs.priority.save")}
        </Button>
        {saved && <span className="text-xs text-muted-foreground">Saved</span>}
      </div>
      {draft > 0 && (
        <p
          className="text-sm rounded-md border border-amber-400/60 bg-amber-50 px-3 py-2 text-amber-900 dark:bg-amber-900/20 dark:text-amber-300"
          role="alert"
        >
          {t("jobs.priority.warning")}
        </p>
      )}
    </div>
  );
}

export function JobDetailShell({
  job,
  children,
}: {
  job: JobRead;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const { currentUser } = useAuth();
  const isAdmin = currentUser?.role === "admin";

  const cancel = useCancelJob();
  const nav = useNavigate();
  const isPending = job.status === "pending" || job.status === "preparing";
  const { data: queuePos } = useJobQueuePosition(job.id, isPending);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">
            {job.type} — {job.id.slice(0, 8)}
          </h1>
          <StatusBadge status={job.status} />
        </div>
        <div className="flex gap-2">
          {job.mlflow_run_id && job.mlflow_experiment_id && (
            <OpenInMlflowButton
              experimentId={job.mlflow_experiment_id}
              runId={job.mlflow_run_id}
            />
          )}
          <Button
            variant="ghost"
            onClick={() => nav(`/jobs/new?from=${job.id}`)}
          >
            Clone
          </Button>
          {!isTerminal(job.status) && (
            <Button variant="destructive" onClick={() => cancel.mutate(job.id)}>
              Cancel
            </Button>
          )}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Metadata</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
          <div>
            <span className="text-muted-foreground">Submitted:</span>{" "}
            {formatRelative(job.submitted_at)}
          </div>
          <div>
            <span className="text-muted-foreground">Duration:</span>{" "}
            {formatDuration(job.started_at, job.finished_at)}
          </div>
          <div>
            <span className="text-muted-foreground">MLflow run:</span>{" "}
            <code>{job.mlflow_run_id ?? "—"}</code>
          </div>
          <div>
            <span className="text-muted-foreground">Failure reason:</span>{" "}
            {job.failure_reason ?? "—"}
          </div>
          {isPending && queuePos?.position != null && (
            <div className="col-span-2">
              <span className="text-muted-foreground">Queue position:</span>{" "}
              <strong>#{queuePos.position}</strong>
            </div>
          )}
          {isAdmin && (
            <div className="col-span-2">
              <span className="text-muted-foreground">
                {t("jobs.priority.label")}:
              </span>{" "}
              <PriorityEditor job={job} />
            </div>
          )}
        </CardContent>
      </Card>

      {children}
    </div>
  );
}
