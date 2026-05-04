import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { MaldetEvent } from "@/hooks/useJobEvents";

type Point = { step: number; [metric: string]: number };

function metricsToSeries(events: MaldetEvent[]): Point[] {
  const byStep = new Map<number, Point>();
  for (const e of events) {
    if (e.kind !== "metric") continue;
    const step = typeof e.step === "number" ? e.step : 0;
    const name = String(e.name ?? "value");
    const value = typeof e.value === "number" ? e.value : Number.NaN;
    if (Number.isNaN(value)) continue;
    const row = byStep.get(step) ?? { step };
    row[name] = value;
    byStep.set(step, row);
  }
  return [...byStep.values()].sort((a, b) => a.step - b.step);
}

export function JobMetricChart({ events }: { events: MaldetEvent[] }) {
  const data = metricsToSeries(events);
  const metrics = new Set<string>();
  for (const d of data) {
    for (const k of Object.keys(d)) if (k !== "step") metrics.add(k);
  }
  if (data.length === 0) {
    return <p className="text-sm text-muted-foreground">No metrics yet.</p>;
  }
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="step" />
        <YAxis />
        <Tooltip />
        <Legend verticalAlign="bottom" height={36} />
        {[...metrics].map((m, i) => (
          <Line
            key={m}
            type="monotone"
            dataKey={m}
            stroke={`hsl(${(i * 70) % 360}, 70%, 45%)`}
            dot={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
