import { Link } from "react-router";
import { useExperiments } from "@/api/queries/runs";
import { Card, CardContent } from "@/components/ui/card";

export const handle = { breadcrumb: "Runs" };

export default function ExperimentsListPage() {
  const { data, isLoading } = useExperiments();
  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Experiments</h1>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {(data ?? []).map((exp) => (
          <Link key={exp.experiment_id} to={`/runs/${exp.experiment_id}`}>
            <Card className="transition hover:border-primary">
              <CardContent className="p-4">
                <div className="text-xs text-muted-foreground">#{exp.experiment_id}</div>
                <div className="text-lg font-medium">{exp.name}</div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
