import { useTranslation } from "react-i18next";
import type { Dataset } from "@/api/queries/datasets";
import { Card, CardContent } from "@/components/ui/card";
import { formatRelative } from "@/lib/date";

interface Props {
  dataset: Dataset;
}

interface Tile {
  label: string;
  value: string;
  numberClass?: string;
}

export function DatasetKpiStrip({ dataset }: Props) {
  const { t } = useTranslation();
  const labels = (dataset.label_distribution ?? {}) as Record<string, number>;
  const families = (dataset.family_distribution ?? {}) as Record<
    string,
    number
  >;

  const tiles: Tile[] = [
    {
      label: t("datasets.detail.kpi.samples"),
      value: dataset.sample_count.toLocaleString(),
    },
    {
      label: t("datasets.detail.kpi.malware"),
      value: (labels["Malware"] ?? 0).toLocaleString(),
      numberClass: "text-red-600 dark:text-red-500",
    },
    {
      label: t("datasets.detail.kpi.benign"),
      value: (labels["Benign"] ?? 0).toLocaleString(),
      numberClass: "text-green-600 dark:text-green-500",
    },
    {
      label: t("datasets.detail.kpi.families"),
      value: Object.keys(families).length.toLocaleString(),
    },
    {
      label: t("datasets.detail.kpi.created"),
      value: formatRelative(dataset.created_at),
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-5">
      {tiles.map((tile) => (
        <Card key={tile.label}>
          <CardContent className="px-4 py-3">
            <div
              className={`text-2xl font-semibold tabular-nums leading-tight ${tile.numberClass ?? ""}`}
            >
              {tile.value}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {tile.label}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
