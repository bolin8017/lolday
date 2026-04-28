import { ReactNode } from "react";
import { useNavigate } from "react-router";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/common/StatusBadge";
import { useCancelJob } from "@/api/queries/jobs";
import { useJobQueuePosition } from "@/api/queries/cluster";
import { isTerminal } from "@/lib/status";
import { formatDuration, formatRelative } from "@/lib/date";

export function JobDetailShell({
  job,
  children,
}: {
  job: any;
  children: ReactNode;
}) {
  const cancel = useCancelJob();
  const nav = useNavigate();
  const isPending = job.status === "pending" || job.status === "preparing";
  const { data: queuePos } = useJobQueuePosition(job.id, isPending);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">{job.type} — {job.id.slice(0, 8)}</h1>
          <StatusBadge status={job.status} />
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => nav(`/jobs/new?from=${job.id}`)}>Clone</Button>
          {!isTerminal(job.status) && (
            <Button variant="destructive" onClick={() => cancel.mutate(job.id)}>Cancel</Button>
          )}
        </div>
      </div>

      <Card>
        <CardHeader><CardTitle>Metadata</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-2 gap-2 text-sm">
          <div><span className="text-muted-foreground">Submitted:</span> {formatRelative(job.submitted_at)}</div>
          <div><span className="text-muted-foreground">Duration:</span> {formatDuration(job.started_at, job.finished_at)}</div>
          <div><span className="text-muted-foreground">MLflow run:</span> <code>{job.mlflow_run_id ?? "—"}</code></div>
          <div><span className="text-muted-foreground">Failure reason:</span> {job.failure_reason ?? "—"}</div>
          {isPending && queuePos?.position != null && (
            <div className="col-span-2">
              <span className="text-muted-foreground">Queue position:</span>{" "}
              <strong>#{queuePos.position}</strong>
            </div>
          )}
        </CardContent>
      </Card>

      {children}
    </div>
  );
}
