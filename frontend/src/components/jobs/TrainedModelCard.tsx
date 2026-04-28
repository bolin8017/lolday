import { Link } from "react-router";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useModelVersionForJob } from "@/api/queries/models";

export function TrainedModelCard({ jobId }: { jobId: string }) {
  const { data, isLoading } = useModelVersionForJob(jobId);
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Trained model</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Loading…
        </CardContent>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Trained model</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Model not yet registered (or registration failed — see backend logs).
        </CardContent>
      </Card>
    );
  }
  const mv = data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Trained model</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 text-sm">
        <div>
          <span className="text-muted-foreground">Registered as:</span>{" "}
          <Link
            to={`/models/${mv.mlflow_name}`}
            className="text-primary hover:underline"
          >
            {mv.mlflow_name} v{mv.mlflow_version}
          </Link>
        </div>
        <div>
          <span className="text-muted-foreground">Stage:</span>{" "}
          {mv.current_stage}
        </div>
      </CardContent>
    </Card>
  );
}
