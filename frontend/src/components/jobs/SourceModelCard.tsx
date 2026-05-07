import { Link } from "react-router";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useModelVersion } from "@/api/queries/models";

export function SourceModelCard({
  sourceModelVersionId,
}: {
  sourceModelVersionId: string;
}) {
  const { data, isLoading, error } = useModelVersion(sourceModelVersionId);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Source model</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Loading…
        </CardContent>
      </Card>
    );
  }
  if (error || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Source model</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Failed to load source model.
        </CardContent>
      </Card>
    );
  }
  const mv = data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Source model</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 text-sm">
        <div>
          <span className="text-muted-foreground">Model:</span>{" "}
          <Link
            to={`/models/${mv.owner}/${mv.name}`}
            className="text-primary hover:underline"
          >
            {`${mv.owner}/${mv.name}`}
          </Link>
        </div>
        <div>
          <span className="text-muted-foreground">Version:</span> v
          {mv.mlflow_version} ({mv.current_stage})
        </div>
        {mv.source_job_id && (
          <div>
            <span className="text-muted-foreground">Trained by:</span>{" "}
            <Link
              to={`/jobs/${mv.source_job_id}`}
              className="text-primary hover:underline"
            >
              job {mv.source_job_id.slice(0, 8)}
            </Link>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
