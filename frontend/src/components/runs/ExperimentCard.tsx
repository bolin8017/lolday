import { Link } from "react-router";
import { Card, CardContent } from "@/components/ui/card";
import { OpenInMlflowButton } from "@/components/common/OpenInMlflowButton";
import { formatRelative } from "@/lib/date";

interface Exp {
  experiment_id: string;
  name: string;
  run_count: number | null;
  best_f1: number | null;
  latest_start_time: number | null;
}

export function ExperimentCard({ exp }: { exp: Exp }) {
  return (
    <Card className="transition hover:border-primary">
      <CardContent className="space-y-2 p-4">
        <Link to={`/runs/${exp.experiment_id}`} className="block">
          <div className="text-xs text-muted-foreground">
            #{exp.experiment_id}
          </div>
          <div className="text-lg font-medium">{exp.name}</div>
          <div className="text-sm text-muted-foreground">
            {exp.run_count ?? "—"} runs · Best F1:{" "}
            {exp.best_f1 != null ? exp.best_f1.toFixed(4) : "—"} ·{" "}
            {exp.latest_start_time != null
              ? formatRelative(new Date(exp.latest_start_time).toISOString())
              : "no runs"}
          </div>
        </Link>
        <div className="flex justify-end">
          <OpenInMlflowButton experimentId={exp.experiment_id} />
        </div>
      </CardContent>
    </Card>
  );
}
