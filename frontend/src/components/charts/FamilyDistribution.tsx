import { useMemo, useState } from "react";
import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  LabelList,
} from "recharts";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { aggregateLongTail } from "./FamilyDistribution.logic";

const PRIMARY = "hsl(var(--primary))";
const MUTED = "hsl(var(--muted-foreground))";

interface Props {
  data: Record<string, number>;
}

type SortKey = "count" | "name";

export function FamilyDistribution({ data }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("count");

  const totalFamilies = Object.keys(data).length;
  const totalSamples = useMemo(
    () => Object.values(data).reduce((a, b) => a + b, 0),
    [data],
  );
  const bars = useMemo(() => aggregateLongTail(data, 10), [data]);

  const allRows = useMemo(() => {
    const rows = Object.entries(data)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .map((r, i) => ({ ...r, rank: i + 1 }));
    return rows;
  }, [data]);

  const filteredRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? allRows.filter((r) => r.name.toLowerCase().includes(q))
      : allRows;
    if (sortKey === "name") {
      return [...filtered].sort((a, b) => a.name.localeCompare(b.name));
    }
    return filtered; // already count-desc
  }, [allRows, query, sortKey]);

  if (totalFamilies === 0)
    return (
      <p className="text-muted-foreground">
        {t("datasets.detail.noFamilyData")}
      </p>
    );

  const containerHeight = Math.min((bars.length + 1) * 36, 360);

  return (
    <div className="space-y-3">
      {totalFamilies > bars.filter((b) => !b.isOther).length && (
        <p className="text-xs text-muted-foreground">
          {t("datasets.detail.topOf", {
            shown: bars.filter((b) => !b.isOther).length,
            total: totalFamilies,
          })}
        </p>
      )}
      <div style={{ width: "100%", height: containerHeight }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={bars}
            layout="vertical"
            margin={{ left: 12, right: 56, top: 4, bottom: 4 }}
            barCategoryGap={4}
          >
            <CartesianGrid strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="name"
              width={120}
              interval={0}
              tick={{ fontSize: 12 }}
            />
            <Tooltip
              cursor={{ fill: "hsl(var(--muted) / 0.3)" }}
              formatter={(value: number) => [
                `${value} (${((value / totalSamples) * 100).toFixed(1)}%)`,
                "",
              ]}
            />
            <Bar dataKey="value" radius={[0, 4, 4, 0]}>
              {bars.map((b) => (
                <Cell key={b.name} fill={b.isOther ? MUTED : PRIMARY} />
              ))}
              <LabelList
                dataKey="value"
                position="right"
                formatter={(value: number) =>
                  `${value} (${((value / totalSamples) * 100).toFixed(1)}%)`
                }
                style={{
                  fill: "hsl(var(--foreground))",
                  fontSize: 12,
                  fontVariantNumeric: "tabular-nums",
                }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {t("datasets.detail.showAllFamilies", { n: totalFamilies })}
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-2 space-y-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("datasets.detail.searchFamilies")}
          />
          <div className="overflow-x-auto rounded border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left">
                    <button
                      type="button"
                      onClick={() => setSortKey("name")}
                      className="hover:text-foreground"
                    >
                      {t("datasets.detail.tableFamily")}
                    </button>
                  </th>
                  <th className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => setSortKey("count")}
                      className="hover:text-foreground"
                    >
                      {t("datasets.detail.tableCount")}
                    </button>
                  </th>
                  <th className="px-3 py-2 text-right">
                    {t("datasets.detail.tablePercent")}
                  </th>
                  <th className="px-3 py-2 text-right">
                    {t("datasets.detail.tableRank")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((r) => (
                  <tr key={r.name} className="border-t">
                    <td className="px-3 py-1.5 font-mono text-xs">{r.name}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {r.value.toLocaleString()}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {((r.value / totalSamples) * 100).toFixed(1)}%
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {r.rank}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
