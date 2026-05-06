import { useNavigate } from "react-router";
import { Download } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useDeleteDataset, type Dataset } from "@/api/queries/datasets";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

interface Props {
  dataset: Dataset;
}

export function DatasetHeader({ dataset }: Props) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const del = useDeleteDataset();
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold leading-tight">
            {dataset.name}
          </h1>
          <Badge variant="outline" className="capitalize">
            {dataset.visibility}
          </Badge>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {dataset.description ?? "—"}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button asChild variant="outline" size="sm">
          <a
            href={`/api/v1/datasets/${dataset.id}/csv`}
            download={`${dataset.name}.csv`}
          >
            <Download className="mr-1 h-4 w-4" />
            {t("datasets.detail.downloadCsv")}
          </a>
        </Button>
        <Button
          variant="destructive"
          size="sm"
          disabled={del.isPending}
          onClick={async () => {
            if (!confirm("Delete this dataset?")) return;
            await del.mutateAsync(dataset.id);
            nav("/datasets");
          }}
        >
          {t("common.delete")}
        </Button>
      </div>
    </header>
  );
}
