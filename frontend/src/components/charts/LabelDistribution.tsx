import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Legend,
  Tooltip,
} from "recharts";

const COLORS = ["#dc2626", "#16a34a", "#f59e0b", "#0ea5e9", "#8b5cf6"];

export function LabelDistribution({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).map(([name, value]) => ({
    name,
    value,
  }));
  if (entries.length === 0)
    return <p className="text-muted-foreground">No label data.</p>;
  return (
    <div style={{ width: "100%", height: 260 }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={entries}
            dataKey="value"
            nameKey="name"
            outerRadius={90}
            label
          >
            {entries.map((_, i) => (
              <Cell key={i} fill={COLORS[i % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip />
          <Legend verticalAlign="bottom" height={36} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
