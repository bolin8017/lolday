import { useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { useRun } from "@/api/queries/runs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricsTable } from "@/components/jobs/MetricsTable";
import { ConfusionMatrix } from "@/components/charts/ConfusionMatrix";
import { ArtifactTree } from "@/components/common/ArtifactTree";
import { JsonTreeView } from "@/components/common/JsonTreeView";

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
      } catch { return null; }
    },
    retry: false,
    enabled: Boolean(runId),
  });
}

export default function RunDetailPage() {
  const { runId = "" } = useParams();
  const { data } = useRun(runId);
  const { data: cm } = useConfusionMatrix(runId);
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  const run = data as unknown as {
    run_id: string; status: string; start_time?: number; end_time?: number;
    metrics?: Record<string, number>; params?: Record<string, unknown>; tags?: Record<string, string>;
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Run {runId.slice(0, 10)}</h1>
      <Card>
        <CardHeader><CardTitle>Metrics</CardTitle></CardHeader>
        <CardContent><MetricsTable metrics={run.metrics ?? {}} /></CardContent>
      </Card>
      {cm && (
        <Card>
          <CardHeader><CardTitle>Confusion matrix</CardTitle></CardHeader>
          <CardContent><ConfusionMatrix labels={cm.labels} matrix={cm.matrix} /></CardContent>
        </Card>
      )}
      <Card>
        <CardHeader><CardTitle>Params</CardTitle></CardHeader>
        <CardContent><JsonTreeView value={run.params ?? {}} /></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Tags</CardTitle></CardHeader>
        <CardContent><JsonTreeView value={run.tags ?? {}} /></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Artifacts</CardTitle></CardHeader>
        <CardContent><ArtifactTree runId={runId} /></CardContent>
      </Card>
    </div>
  );
}
