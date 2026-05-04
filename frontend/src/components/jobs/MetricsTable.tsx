import { Card, CardContent } from "@/components/ui/card";

const STANDARD_ORDER = ["accuracy", "precision", "recall", "f1"] as const;

const HUMAN_LABELS: Record<string, string> = {
  accuracy: "Accuracy",
  precision: "Precision",
  recall: "Recall",
  f1: "F1",
  f1_score: "F1",
  roc_auc: "ROC AUC",
  pr_auc: "PR AUC",
};

function humanize(key: string): string {
  if (HUMAN_LABELS[key]) return HUMAN_LABELS[key];
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function MetricsTable({ metrics }: { metrics: Record<string, number> }) {
  const entries = Object.entries(metrics).filter(
    ([, v]) => typeof v === "number",
  );
  if (entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No metrics recorded for this job.
      </p>
    );
  }
  const standard = STANDARD_ORDER.filter((k) => k in metrics).map(
    (k) => [k, metrics[k]] as const,
  );
  const rest = entries
    .filter(([k]) => !(STANDARD_ORDER as readonly string[]).includes(k))
    .sort(([a], [b]) => a.localeCompare(b));
  const ordered = [...standard, ...rest];

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-4">
      {ordered.map(([k, v]) => (
        <Card key={k} data-testid="metric-card" data-name={k}>
          <CardContent className="p-4">
            <div className="text-xs uppercase text-muted-foreground">
              {humanize(k)}
            </div>
            <div className="text-2xl font-semibold">
              {(v as number).toFixed(4)}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
