import { PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import { useTranslation } from "react-i18next";

const LABEL_COLOR: Record<string, string> = {
  Malware: "#dc2626", // red-600
  Benign: "#16a34a", // green-600
};
const FALLBACK_COLOR = "hsl(var(--muted-foreground))";

const LABEL_ORDER = ["Malware", "Benign"];

interface Entry {
  name: string;
  value: number;
}

function colorFor(name: string): string {
  return LABEL_COLOR[name] ?? FALLBACK_COLOR;
}

function sortEntries(data: Record<string, number>): Entry[] {
  const known: Entry[] = [];
  const unknown: Entry[] = [];
  for (const [name, value] of Object.entries(data)) {
    if (LABEL_ORDER.includes(name)) known.push({ name, value });
    else unknown.push({ name, value });
  }
  known.sort(
    (a, b) => LABEL_ORDER.indexOf(a.name) - LABEL_ORDER.indexOf(b.name),
  );
  unknown.sort((a, b) => a.name.localeCompare(b.name));
  return [...known, ...unknown];
}

export function LabelDistribution({ data }: { data: Record<string, number> }) {
  const { t } = useTranslation();
  const entries = sortEntries(data);
  const total = entries.reduce((acc, e) => acc + e.value, 0);
  if (entries.length === 0 || total === 0)
    return (
      <p className="text-muted-foreground">
        {t("datasets.detail.noLabelData")}
      </p>
    );

  const dominant = [...entries].sort((a, b) => b.value - a.value)[0];
  const dominantPct = Math.round((dominant.value / total) * 100);

  return (
    <div className="flex flex-col items-stretch gap-4 sm:flex-row sm:items-center">
      <div className="relative h-[200px] w-full sm:w-[200px]">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={entries}
              dataKey="value"
              nameKey="name"
              innerRadius={60}
              outerRadius={90}
              isAnimationActive={false}
              stroke="hsl(var(--background))"
              strokeWidth={2}
            >
              {entries.map((e) => (
                <Cell key={e.name} fill={colorFor(e.name)} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div
          className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center"
          aria-hidden
        >
          <span className="text-xl font-semibold tabular-nums">
            {dominantPct}%
          </span>
          <span className="text-xs text-muted-foreground">{dominant.name}</span>
        </div>
      </div>
      <ul className="grid flex-1 grid-cols-[1fr_auto_auto] gap-x-4 gap-y-1 text-sm">
        {entries.map((e) => {
          const pct = ((e.value / total) * 100).toFixed(1);
          return (
            <li key={e.name} className="contents">
              <span className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="inline-block h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: colorFor(e.name) }}
                />
                {e.name}
              </span>
              <span className="text-right tabular-nums text-muted-foreground">
                {e.value.toLocaleString()}
              </span>
              <span className="text-right tabular-nums text-muted-foreground">
                {pct}%
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
