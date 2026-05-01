import { useExperimentsWithStats } from "@/api/queries/runs";
import { ExperimentCard } from "@/components/runs/ExperimentCard";

export const handle = { breadcrumb: "Runs" };

export default function ExperimentsListPage() {
  const { data, isLoading } = useExperimentsWithStats();
  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Experiments</h1>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {(data ?? []).map((exp) => (
          <ExperimentCard key={exp.experiment_id} exp={exp} />
        ))}
      </div>
    </div>
  );
}
