import { useParams } from "react-router";
import {
  useModelDetail,
  useModelVersions,
  type ModelVersion,
  type Stage,
} from "@/api/queries/models";
import { DataTable } from "@/components/tables/DataTable";
import { Badge } from "@/components/ui/badge";
import { ModelTransitionDialog } from "@/components/forms/ModelTransitionDialog";
import { formatRelative } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Model" };

export default function ModelDetailPage() {
  const params = useParams();
  const name = decodeURIComponent(params.name ?? "");
  // TODO(T26): this route is replaced by _authed.models.$owner.$name.tsx;
  // owner param will come from the URL. Using empty string here so typecheck
  // passes until T26 lands.
  const owner = "";
  const { data: model } = useModelDetail(owner, name);
  const { data: versionsData } = useModelVersions(owner, name);
  const versionsArr = (versionsData as { items?: ModelVersion[] })?.items ?? [];
  const existingProd = versionsArr.find(
    (v) => v.current_stage === "Production",
  );

  const columns: ColumnDef<ModelVersion>[] = [
    {
      accessorKey: "mlflow_version",
      header: "Version",
      cell: ({ row }) => `v${row.original.mlflow_version}`,
    },
    {
      accessorKey: "current_stage",
      header: "Stage",
      cell: ({ row }) => <Badge>{row.original.current_stage}</Badge>,
    },
    {
      id: "run",
      header: "Source run",
      cell: ({ row }) => (
        <span className="font-mono text-xs text-muted-foreground">
          {row.original.mlflow_run_id.slice(0, 10)}
        </span>
      ),
    },
    {
      accessorKey: "created_at",
      header: "Created",
      cell: ({ row }) => formatRelative(row.original.created_at),
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <ModelTransitionDialog
          owner={owner}
          modelName={name}
          version={row.original.mlflow_version}
          currentStage={row.original.current_stage as Stage}
          hasExistingProd={Boolean(
            existingProd &&
            existingProd.mlflow_version !== row.original.mlflow_version,
          )}
        />
      ),
    },
  ];

  if (!model) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">{name}</h1>
      <DataTable
        data={versionsArr}
        columns={columns}
        emptyMessage="No versions registered."
      />
    </div>
  );
}
