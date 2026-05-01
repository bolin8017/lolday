import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Download } from "lucide-react";
import { SourceModelCard } from "./SourceModelCard";
import { PredictionSummaryCard } from "./PredictionSummaryCard";
import { ResolvedConfigCard } from "./ResolvedConfigCard";
import type { components } from "@/api/schema.gen";

type JobRead = components["schemas"]["JobRead"];

export function PredictSummary({ job }: { job: JobRead }) {
  const sm = (job.summary_metrics ?? {}) as Record<string, unknown>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- prediction_summary has PredictionSummary shape at runtime
  const ps = sm.prediction_summary as any;

  return (
    <>
      {job.source_model_version_id && (
        <SourceModelCard sourceModelVersionId={job.source_model_version_id} />
      )}
      <PredictionSummaryCard summary={ps ?? null} />
      {job.mlflow_run_id && (
        <Card>
          <CardHeader>
            <CardTitle>Output</CardTitle>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline">
              <a
                href={`/api/v1/runs/${job.mlflow_run_id}/artifacts/download?path=predictions.csv`}
                download
              >
                <Download className="mr-2 h-4 w-4" />
                Download predictions.csv
              </a>
            </Button>
          </CardContent>
        </Card>
      )}
      <ResolvedConfigCard
        resolvedConfig={job.resolved_config}
        userParams={job.user_params}
        detectorDefaults={job.detector_defaults}
      />
    </>
  );
}
