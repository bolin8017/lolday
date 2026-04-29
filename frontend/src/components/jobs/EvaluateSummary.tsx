import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricsTable } from "./MetricsTable";
import { PerClassMetrics } from "./PerClassMetrics";
import { ConfusionMatrix } from "@/components/charts/ConfusionMatrix";
import { SourceModelCard } from "./SourceModelCard";
import { ResolvedConfigCard } from "./ResolvedConfigCard";

export function EvaluateSummary({ job }: { job: any }) {
  const sm = (job.summary_metrics ?? {}) as Record<string, unknown>;
  const metrics = (sm.metrics as Record<string, number>) ?? {};
  const perClass = sm.per_class as Record<string, any> | undefined;
  const cm = sm.confusion_matrix as
    | { labels?: string[]; matrix?: number[][] }
    | undefined;

  return (
    <>
      {job.source_model_version_id && (
        <SourceModelCard sourceModelVersionId={job.source_model_version_id} />
      )}
      <Card>
        <CardHeader>
          <CardTitle>Evaluation metrics</CardTitle>
        </CardHeader>
        <CardContent>
          <MetricsTable metrics={metrics} />
        </CardContent>
      </Card>
      {perClass && (
        <Card>
          <CardHeader>
            <CardTitle>Per-class metrics</CardTitle>
          </CardHeader>
          <CardContent>
            <PerClassMetrics perClass={perClass} />
          </CardContent>
        </Card>
      )}
      {cm?.labels && cm.matrix && (
        <Card>
          <CardHeader>
            <CardTitle>Confusion matrix</CardTitle>
          </CardHeader>
          <CardContent>
            <ConfusionMatrix labels={cm.labels} matrix={cm.matrix} />
          </CardContent>
        </Card>
      )}
      <ResolvedConfigCard
        resolvedConfig={job.resolved_config}
        userParams={job.user_params ?? null}
      />
    </>
  );
}
