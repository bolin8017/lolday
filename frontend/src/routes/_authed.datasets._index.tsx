import { Link } from "react-router";
import { useState } from "react";
import { useDatasets, type Dataset } from "@/api/queries/datasets";
import { DataTable } from "@/components/tables/DataTable";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatRelative } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";

export const handle = { breadcrumb: "Datasets" };

const columns: ColumnDef<Dataset>[] = [
  { accessorKey: "name", header: "Name" },
  {
    accessorKey: "visibility",
    header: "Visibility",
    cell: ({ row }) => (
      <Badge variant={row.original.visibility === "public" ? "default" : "secondary"}>
        {row.original.visibility}
      </Badge>
    ),
  },
  { accessorKey: "sample_count", header: "Samples" },
  {
    accessorKey: "size_bytes",
    header: "Size",
    cell: ({ row }) => {
      const bytes = row.original.size_bytes;
      return bytes != null ? `${(bytes / 1024).toFixed(1)} KB` : "—";
    },
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => formatRelative(row.original.created_at),
  },
];

export default function DatasetsListPage() {
  const [visibility, setVisibility] = useState<"public" | "private" | "all">("all");
  const { data, isLoading } = useDatasets(visibility);

  const items: Dataset[] = (data as { items?: Dataset[] } | undefined)?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Datasets</h1>
        <div className="flex items-center gap-2">
          <Select value={visibility} onValueChange={(v) => setVisibility(v as typeof visibility)}>
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="public">Public</SelectItem>
              <SelectItem value="private">Mine (private)</SelectItem>
            </SelectContent>
          </Select>
          <Button asChild>
            <Link to="/datasets/new">
              <Plus className="mr-2 h-4 w-4" />
              Upload
            </Link>
          </Button>
        </div>
      </div>
      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : (
        <DataTable
          data={items}
          columns={columns}
          emptyMessage="No datasets yet."
          onRowClick={(d) => {
            window.location.href = `/datasets/${d.id}`;
          }}
        />
      )}
    </div>
  );
}
