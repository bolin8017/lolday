import { useParams, Link, useNavigate } from "react-router";
import { useJob, useJobLogs, useCancelJob } from "@/api/queries/jobs";
import { useJobQueuePosition } from "@/api/queries/cluster";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/common/StatusBadge";
import { MetricCards } from "@/components/charts/MetricCards";
import { ConfusionMatrix } from "@/components/charts/ConfusionMatrix";
import { ArtifactTree } from "@/components/common/ArtifactTree";
import { LogTail } from "@/components/common/LogTail";
import { JsonViewer } from "@/components/common/JsonViewer";
import { formatDuration, formatRelative } from "@/lib/date";
import { isTerminal } from "@/lib/status";

export const handle = { breadcrumb: "Job" };

export default function JobDetailPage() {
  const { id = "" } = useParams();
  const { data: job } = useJob(id);
  const { data: logText } = useJobLogs(id, job?.status);
  const cancel = useCancelJob();
  const nav = useNavigate();
  const isPending = job?.status === "pending" || job?.status === "preparing";
  const { data: queuePos } = useJobQueuePosition(id, isPending);
  if (!job) return <p className="text-muted-foreground">Loading…</p>;

  const sm = (job.summary_metrics ?? {}) as Record<string, unknown>;
  const metrics = (typeof sm.metrics === "object" && sm.metrics) ? sm.metrics as Record<string, number> : {};
  const cm = (sm.confusion_matrix as { labels?: string[]; matrix?: number[][] } | undefined);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">{job.type} — {id.slice(0, 8)}</h1>
          <StatusBadge status={job.status} />
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => nav(`/jobs/new?from=${id}`)}>Clone</Button>
          {!isTerminal(job.status) && (
            <Button variant="destructive" onClick={() => cancel.mutate(id)}>Cancel</Button>
          )}
        </div>
      </div>

      <Tabs defaultValue="summary">
        <TabsList>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
          <TabsTrigger value="artifacts" disabled={!job.mlflow_run_id}>Artifacts</TabsTrigger>
          {job.mlflow_run_id && (
            <TabsTrigger value="mlflow" asChild>
              <Link to={`/runs/${job.mlflow_experiment_id}/${job.mlflow_run_id}`}>Open run ↗</Link>
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="summary" className="space-y-4">
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
          <Card>
            <CardHeader><CardTitle>Metrics</CardTitle></CardHeader>
            <CardContent><MetricCards metrics={metrics} /></CardContent>
          </Card>
          {cm?.labels && cm.matrix && (
            <Card>
              <CardHeader><CardTitle>Confusion matrix</CardTitle></CardHeader>
              <CardContent><ConfusionMatrix labels={cm.labels} matrix={cm.matrix} /></CardContent>
            </Card>
          )}
          <Card>
            <CardHeader><CardTitle>Resolved config</CardTitle></CardHeader>
            <CardContent><JsonViewer value={job.resolved_config} /></CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="logs">
          <LogTail text={(logText as string) ?? ""} />
        </TabsContent>

        <TabsContent value="artifacts">
          {job.mlflow_run_id ? (
            <ArtifactTree runId={job.mlflow_run_id} />
          ) : (
            <p className="text-muted-foreground">No MLflow run recorded for this job.</p>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
