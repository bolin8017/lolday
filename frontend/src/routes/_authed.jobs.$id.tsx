import { useParams, Link } from "react-router";
import { useJob, useJobLogs } from "@/api/queries/jobs";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LogTail } from "@/components/common/LogTail";
import { ArtifactTree } from "@/components/common/ArtifactTree";
import { JobDetailShell } from "@/components/jobs/JobDetailShell";
import { TrainSummary } from "@/components/jobs/TrainSummary";
import { EvaluateSummary } from "@/components/jobs/EvaluateSummary";
import { PredictSummary } from "@/components/jobs/PredictSummary";

export const handle = { breadcrumb: "Job" };

export default function JobDetailPage() {
  const { id = "" } = useParams();
  const { data: job } = useJob(id);
  const { data: logText } = useJobLogs(id, job?.status);
  if (!job) return <p className="text-muted-foreground">Loading…</p>;

  return (
    <JobDetailShell job={job}>
      <Tabs defaultValue="summary">
        <TabsList>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
          <TabsTrigger value="artifacts" disabled={!job.mlflow_run_id}>
            Artifacts
          </TabsTrigger>
          {job.mlflow_run_id && (
            <TabsTrigger value="mlflow" asChild>
              <Link
                to={`/runs/${job.mlflow_experiment_id}/${job.mlflow_run_id}`}
              >
                Open run ↗
              </Link>
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="summary" className="space-y-4">
          {job.type === "train" && <TrainSummary job={job} />}
          {job.type === "evaluate" && <EvaluateSummary job={job} />}
          {job.type === "predict" && <PredictSummary job={job} />}
        </TabsContent>

        <TabsContent value="logs">
          <LogTail text={(logText as string) ?? ""} />
        </TabsContent>

        <TabsContent value="artifacts">
          {job.mlflow_run_id ? (
            <ArtifactTree runId={job.mlflow_run_id} />
          ) : (
            <p className="text-muted-foreground">
              No MLflow run recorded for this job.
            </p>
          )}
        </TabsContent>
      </Tabs>
    </JobDetailShell>
  );
}
