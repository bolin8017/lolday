import { Link } from "react-router";
import { useDetectors, type Detector } from "@/api/queries/detectors";
import { DataTable } from "@/components/tables/DataTable";
import { Button } from "@/components/ui/button";
import { formatRelative } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";

export const handle = { breadcrumb: "Detectors" };

const columns: ColumnDef<Detector>[] = [
  { accessorKey: "display_name", header: "Name" },
  { accessorKey: "description", header: "Description",
    cell: ({ row }) => <span className="text-muted-foreground">{row.original.description ?? "—"}</span> },
  { accessorKey: "git_url", header: "Git URL",
    cell: ({ row }) => <span className="font-mono text-xs">{row.original.git_url}</span> },
  { accessorKey: "created_at", header: "Created",
    cell: ({ row }) => formatRelative(row.original.created_at) },
];

export default function DetectorsListPage() {
  const { data, isLoading } = useDetectors();
  const items = (data as { items?: Detector[] } | undefined)?.items ?? [];
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Detectors</h1>
        <Button asChild><Link to="/detectors/new"><Plus className="mr-2 h-4 w-4" />Register</Link></Button>
      </div>
      {isLoading ? <p className="text-muted-foreground">Loading…</p> : (
        <DataTable
          data={items}
          columns={columns}
          emptyMessage="No detectors registered yet."
          onRowClick={(d) => { window.location.href = `/detectors/${d.id}`; }}
        />
      )}
    </div>
  );
}
