import { useEffect } from "react";
import { Navigate, useParams } from "react-router";
import { useRun } from "@/api/queries/runs";

export const handle = { breadcrumb: "Run" };

export default function RunRedirectPage() {
  const { expId = "", runId = "" } = useParams();
  const { data, isLoading, error } = useRun(runId);

  // External redirect for orphan runs (no lolday.job_id tag) — go to MLflow native UI.
  // useEffect because window.location.replace must run after mount, not during render.
  const run = data as { tags?: Record<string, string> } | null;
  const jobId =
    run?.tags?.["lolday.job_id"] ?? run?.tags?.lolday_job_id ?? null;
  const orphan = !!data && !jobId;
  useEffect(() => {
    if (orphan) {
      window.location.replace(`/mlflow/#/experiments/${expId}/runs/${runId}`);
    }
  }, [orphan, expId, runId]);

  if (isLoading) {
    return <p className="text-muted-foreground">Loading…</p>;
  }
  if (error || !data) {
    return <Navigate to="/runs" replace />;
  }
  if (jobId) {
    return <Navigate to={`/jobs/${jobId}`} replace />;
  }
  return <p className="text-muted-foreground">Redirecting to MLflow…</p>;
}
