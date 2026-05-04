import { Link } from "react-router";
import {
  useRegisteredModels,
  type RegisteredModel,
} from "@/api/queries/models";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Models" };

const columns: ColumnDef<RegisteredModel>[] = [
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
    header: "Staging",
    cell: ({ row }) =>
      row.original.latest_staging_version != null ? (
        <Badge variant="secondary">
          v{row.original.latest_staging_version}
        </Badge>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
    meta: { cardLabel: "Staging", cardSlot: "body" },
  },
  {
    id: "prod",
    header: "Production",
    cell: ({ row }) =>
      row.original.latest_production_version != null ? (
        <Badge className="bg-emerald-600">
          v{row.original.latest_production_version}
        </Badge>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
    meta: { cardLabel: "Production", cardSlot: "body" },
  },
];

export default function ModelsListPage() {
  const { data, isLoading } = useRegisteredModels();
  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <PageHeader title="Models" />
      <DataTable
        data={data ?? []}
        columns={columns}
        emptyMessage="No models registered yet."
      />
    </div>
  );
}
