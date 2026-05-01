import { useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { useRun } from "@/api/queries/runs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricsTable } from "@/components/jobs/MetricsTable";
import { ConfusionMatrix } from "@/components/charts/ConfusionMatrix";
import { ArtifactTree } from "@/components/common/ArtifactTree";
import { JsonTreeView } from "@/components/common/JsonTreeView";
import { CollapsibleCard } from "@/components/common/CollapsibleCard";
import { OpenInMlflowButton } from "@/components/common/OpenInMlflowButton";
import { OpenInLoldayJobButton } from "@/components/common/OpenInLoldayJobButton";

export const handle = { breadcrumb: "Run" };

function useConfusionMatrix(runId: string) {
  return useQuery({
    queryKey: ["runs", runId, "cm-artifact"],
    queryFn: async () => {
      try {
        const resp = await fetch(
          `/api/v1/runs/${runId}/artifacts/download?path=confusion_matrix.json`,
          { credentials: "include" },
        );
        if (!resp.ok) return null;
        return (await resp.json()) as { labels: string[]; matrix: number[][] };
      } catch {
        return null;
      }
    },
    retry: false,
    enabled: Boolean(runId),
  });
}

export default function RunDetailPage() {
  const { expId = "", runId = "" } = useParams();
  const { data } = useRun(runId);
  const { data: cm } = useConfusionMatrix(runId);
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  const run = data as unknown as {
    run_id: string;
    status: string;
    start_time?: number;
    end_time?: number;
    metrics?: Record<string, number>;
    params?: Record<string, unknown>;
    tags?: Record<string, string>;
  };

  const jobId = run.tags?.["lolday.job_id"] ?? run.tags?.lolday_job_id;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Run {runId.slice(0, 10)}</h1>
        <div className="flex gap-2">
          {jobId && <OpenInLoldayJobButton jobId={jobId} />}
          <OpenInMlflowButton experimentId={expId} runId={runId} />
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Metrics</CardTitle>
        </CardHeader>
        <CardContent>
          <MetricsTable metrics={run.metrics ?? {}} />
        </CardContent>
      </Card>

      {cm && (
        <Card>
          <CardHeader>
            <CardTitle>Confusion matrix</CardTitle>
          </CardHeader>
          <CardContent>
            <ConfusionMatrix labels={cm.labels} matrix={cm.matrix} />
          </CardContent>
        </Card>
      )}

      <CollapsibleCard title="Parameters">
        <JsonTreeView value={run.params ?? {}} collapsed={1} />
      </CollapsibleCard>

      <CollapsibleCard title="Tags">
        <JsonTreeView value={run.tags ?? {}} collapsed={1} />
      </CollapsibleCard>

      <Card>
        <CardHeader>
          <CardTitle>Artifacts</CardTitle>
        </CardHeader>
        <CardContent>
          <ArtifactTree runId={runId} />
        </CardContent>
      </Card>
    </div>
  );
}
