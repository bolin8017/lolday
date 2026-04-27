type SummaryMetrics = {
  metrics?: Record<string, number>;
  confusion_matrix?: unknown;
};

interface Props {
  summaryMetrics: SummaryMetrics | null | undefined;
}

export function FinalMetricsTile({ summaryMetrics }: Props) {
  const metrics = summaryMetrics?.metrics ?? {};
  const entries = Object.entries(metrics);
  if (entries.length === 0) {
    return <span className="text-muted-foreground">—</span>;
  }
  const shown = entries.slice(0, 2);
  const more = entries.length - shown.length;
  return (
    <div className="flex flex-wrap gap-1 text-xs">
      {shown.map(([k, v]) => (
        <span key={k} className="rounded border border-border px-1 py-0.5">
          {k}: {Number(v).toFixed(3)}
        </span>
      ))}
      {more > 0 && <span className="text-muted-foreground">+{more}</span>}
    </div>
  );
}
