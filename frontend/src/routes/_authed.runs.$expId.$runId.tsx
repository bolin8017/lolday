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
      // L-location-replace-encode: percent-encode path segments before
      // interpolating into the URL fragment. /mlflow/ is the reverse-
      // proxied MLflow UI (NOT a SPA route), so useNavigate doesn't apply --
      // but defense-in-depth encoding remains useful for any future schema
      // that allows special characters in IDs.
      window.location.replace(
        `/mlflow/#/experiments/${encodeURIComponent(expId)}/runs/${encodeURIComponent(runId)}`,
      );
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
