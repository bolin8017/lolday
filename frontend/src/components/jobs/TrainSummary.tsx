import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricsTable } from "./MetricsTable";
import { PerClassMetrics } from "./PerClassMetrics";
import { ConfusionMatrix } from "@/components/charts/ConfusionMatrix";
import { JobMetricChart } from "@/components/charts/JobMetricChart";
import { TrainedModelCard } from "./TrainedModelCard";
import { ResolvedConfigCard } from "./ResolvedConfigCard";
import { useJobEvents } from "@/hooks/useJobEvents";
import { NON_TERMINAL_JOB_STATUSES } from "@/lib/status";

export function TrainSummary({ job }: { job: any }) {
  const sm = (job.summary_metrics ?? {}) as Record<string, unknown>;
  const metrics = (sm.metrics as Record<string, number>) ?? {};
  const perClass = sm.per_class as Record<string, any> | undefined;
  const cm = sm.confusion_matrix as
    | { labels?: string[]; matrix?: number[][] }
    | undefined;

  const isLive = (NON_TERMINAL_JOB_STATUSES as readonly string[]).includes(
    job.status,
  );
  const { events, error: eventsError } = useJobEvents(job.id, isLive);
  const hasTimeSeries = events.some(
    (e) =>
      e.kind === "metric" &&
      typeof (e as any).step === "number" &&
      (e as any).step >= 1,
  );

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Final metrics</CardTitle>
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
      {(hasTimeSeries || eventsError) && (
        <Card>
          <CardHeader>
            <CardTitle>Live metrics</CardTitle>
          </CardHeader>
          <CardContent>
            {eventsError && (
              <p className="text-sm text-destructive">{eventsError}</p>
            )}
            {hasTimeSeries && <JobMetricChart events={events} />}
          </CardContent>
        </Card>
      )}
      <TrainedModelCard jobId={job.id} />
      <ResolvedConfigCard
        resolvedConfig={job.resolved_config}
        userParams={job.user_params ?? null}
      />
    </>
  );
}
