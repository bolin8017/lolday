import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface PredictionSummary {
  total: number;
  distribution: Record<string, number>;
  duration_seconds: number | null;
}

const BAR_COLORS = [
  "bg-blue-500",
  "bg-emerald-500",
  "bg-amber-500",
  "bg-rose-500",
];

export function PredictionSummaryCard({
  summary,
  positiveClass,
}: {
  summary: PredictionSummary | null;
  positiveClass?: string;
}) {
  if (!summary) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Predictions</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Prediction summary not available (legacy job or predict failed).
        </CardContent>
      </Card>
    );
  }
  const { total, distribution, duration_seconds } = summary;
  // Order: positive class first (so the row a security operator cares about
  // anchors the bar's left edge and the grid's first cell), then remaining
  // classes by descending count. Matches the (positive)-first row ordering
  // in PerClassMetrics so the two cards read consistently.
  const entries = Object.entries(distribution).sort(([a, ac], [b, bc]) => {
    if (positiveClass) {
      if (a === positiveClass) return -1;
      if (b === positiveClass) return 1;
    }
    return bc - ac;
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Predictions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex items-baseline gap-4">
          <div>
            <div className="text-xs text-muted-foreground">Total samples</div>
            <div className="text-2xl font-semibold">
              {total.toLocaleString()}
            </div>
          </div>
          {duration_seconds != null && (
            <div>
              <div className="text-xs text-muted-foreground">Duration</div>
              <div className="text-2xl font-semibold">
                {duration_seconds.toFixed(1)}s
              </div>
            </div>
          )}
        </div>

        <div>
          <div className="mb-1 text-xs text-muted-foreground">
            Predicted class distribution
          </div>
          <div className="flex h-5 overflow-hidden rounded-md border">
            {entries.map(([cls, count], idx) => {
              const pct = total > 0 ? (count / total) * 100 : 0;
              const color = BAR_COLORS[idx % BAR_COLORS.length];
              return (
                <div
                  key={cls}
                  className={color}
                  style={{ width: `${pct}%` }}
                  title={`${cls}: ${count} (${pct.toFixed(1)}%)`}
                />
              );
            })}
          </div>
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-4">
            {entries.map(([cls, count]) => {
              const pct = total > 0 ? (count / total) * 100 : 0;
              return (
                <div key={cls} className="text-xs">
                  <span className="font-medium">{cls}</span>:{" "}
                  {count.toLocaleString()} ({pct.toFixed(1)}%)
                </div>
              );
            })}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
