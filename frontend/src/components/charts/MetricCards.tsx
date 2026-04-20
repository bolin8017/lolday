import { Card, CardContent } from "@/components/ui/card";

export function MetricCards({ metrics }: { metrics: Record<string, number> }) {
  const keys = ["accuracy", "precision", "recall", "f1", "f1_score"];
  const entries = keys
    .map((k) => [k, metrics[k]] as const)
    .filter(([, v]) => typeof v === "number");
  if (entries.length === 0) return <p className="text-muted-foreground text-sm">No metrics recorded yet.</p>;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {entries.map(([k, v]) => (
        <Card key={k}>
          <CardContent className="p-4">
            <div className="text-xs uppercase text-muted-foreground">{k.replace("_score", "")}</div>
            <div className="text-2xl font-semibold">{(v as number).toFixed(4)}</div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
