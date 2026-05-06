import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { HelpCircle, X } from "lucide-react";
import {
  useRegisteredModels,
  type RegisteredModel,
} from "@/api/queries/models";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Models" };

const DISMISSED_KEY = "lolday.modelsExplainerDismissed";

function HeaderWithTooltip({ label, hint }: { label: string; hint: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {label}
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground"
            aria-label={`${label} info`}
          >
            <HelpCircle size={14} />
          </button>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">{hint}</TooltipContent>
      </Tooltip>
    </span>
  );
}

function buildColumns(t: (k: string) => string): ColumnDef<RegisteredModel>[] {
  return [
    {
      accessorKey: "name",
      header: "Name",
      cell: ({ row }) => (
        <Link
          to={`/models/${encodeURIComponent(row.original.name)}`}
          className="font-medium hover:underline"
        >
          {row.original.name}
        </Link>
      ),
      meta: { cardSlot: "title" },
    },
    {
      accessorKey: "latest_version",
      header: "Latest version",
      meta: { cardLabel: "Latest", cardSlot: "body" },
    },
    {
      id: "staging",
      header: () => (
        <HeaderWithTooltip
          label="Staging"
          hint={t("models.stages.stagingTooltip")}
        />
      ),
      cell: ({ row }) =>
        row.original.latest_staging_version != null ? (
          <Badge variant="secondary">
            v{row.original.latest_staging_version}
          </Badge>
        ) : (
          <span className="text-muted-foreground">
            {t("models.notPromoted")}
          </span>
        ),
      meta: { cardLabel: "Staging", cardSlot: "body" },
    },
    {
      id: "prod",
      header: () => (
        <HeaderWithTooltip
          label="Production"
          hint={t("models.stages.productionTooltip")}
        />
      ),
      cell: ({ row }) =>
        row.original.latest_production_version != null ? (
          <Badge className="bg-emerald-600">
            v{row.original.latest_production_version}
          </Badge>
        ) : (
          <span className="text-muted-foreground">
            {t("models.notPromoted")}
          </span>
        ),
      meta: { cardLabel: "Production", cardSlot: "body" },
    },
  ];
}

function StageExplainerAlert() {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(DISMISSED_KEY) === "1",
  );

  if (dismissed) return null;
  return (
    <Alert className="relative">
      <AlertTitle>{t("models.stagesExplainer.title")}</AlertTitle>
      <AlertDescription className="pr-8">
        {t("models.stagesExplainer.body")}
      </AlertDescription>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="absolute right-2 top-2 h-7 w-7 p-0"
        aria-label={t("common.dismiss")}
        onClick={() => {
          localStorage.setItem(DISMISSED_KEY, "1");
          setDismissed(true);
        }}
      >
        <X size={14} />
      </Button>
    </Alert>
  );
}

export default function ModelsListPage() {
  const { t } = useTranslation();
  const { data, isLoading } = useRegisteredModels();
  const columns = buildColumns(t);
  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <TooltipProvider delayDuration={150}>
      <div className="space-y-4">
        <PageHeader title="Models" />
        <StageExplainerAlert />
        <DataTable
          data={data ?? []}
          columns={columns}
          emptyMessage="No models registered yet."
        />
      </div>
    </TooltipProvider>
  );
}
