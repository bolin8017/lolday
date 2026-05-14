import { useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  useDeleteDetector,
  useDetectors,
  type Detector,
} from "@/api/queries/detectors";
import { DeleteConfirmDialog } from "@/components/common/DeleteConfirmDialog";
import { detailToDeleteBanner } from "@/components/common/deleteErrorBanner";
import { LoldayApiError } from "@/api/errors";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatRelative } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";
import { MoreHorizontal, Plus } from "lucide-react";

export const handle = { breadcrumb: "Detectors" };

function DetectorRowActions({
  detector,
}: {
  // Phase 13a fix (PR review I1): confirmText must be the slug `name` so
  // typing it is feasible (display_name may have spaces, mixed case).
  // Title can show the friendlier `display_name` for context.
  detector: { id: string; name: string; display_name: string };
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<ReturnType<
    typeof detailToDeleteBanner
  > | null>(null);
  const deleteMut = useDeleteDetector();

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm">
            <MoreHorizontal className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            className="text-destructive focus:text-destructive"
            onSelect={(e) => {
              e.preventDefault();
              setOpen(true);
            }}
          >
            Delete detector
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <DeleteConfirmDialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
        title={`Delete detector ${detector.display_name}?`}
        description={
          <>
            This soft-deletes the detector. All versions and Harbor images will
            be permanently purged. Historical jobs and runs remain visible but
            will reference a deleted detector.
          </>
        }
        confirmText={detector.name}
        onConfirm={async () => {
          try {
            await deleteMut.mutateAsync(detector.id);
            setOpen(false);
          } catch (e) {
            const detail =
              e instanceof LoldayApiError ? e.structuredDetail : undefined;
            setError(detailToDeleteBanner(detail));
          }
        }}
        pending={deleteMut.isPending}
        errorBanner={error}
      />
    </>
  );
}

const columns: ColumnDef<Detector>[] = [
  { accessorKey: "display_name", header: "Name", meta: { cardSlot: "title" } },
  {
    accessorKey: "description",
    header: "Description",
    cell: ({ row }) => (
      <span className="text-muted-foreground">
        {row.original.description ?? "—"}
      </span>
    ),
    meta: { cardLabel: "Description", cardSlot: "body" },
  },
  {
    accessorKey: "git_url",
    header: "Git URL",
    cell: ({ row }) => (
      <span
        className="block max-w-full break-all font-mono text-xs md:truncate md:break-normal"
        title={row.original.git_url}
      >
        {row.original.git_url}
      </span>
    ),
    meta: { cardLabel: "Git URL", cardSlot: "body" },
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => formatRelative(row.original.created_at),
    meta: { cardLabel: "Created", cardSlot: "subtitle" },
  },
  {
    id: "actions",
    header: "",
    cell: ({ row }) => <DetectorRowActions detector={row.original} />,
    meta: { cardSlot: "actions" },
  },
];

export default function DetectorsListPage() {
  const navigate = useNavigate();
  const { data, isLoading } = useDetectors();
  const items = (data as { items?: Detector[] } | undefined)?.items ?? [];
  return (
    <div className="space-y-4">
      <PageHeader
        title="Detectors"
        actions={
          <Button asChild>
            <Link to="/detectors/new">
              <Plus className="mr-2 h-4 w-4" />
              Register
            </Link>
          </Button>
        }
      />
      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : (
        <DataTable
          data={items}
          columns={columns}
          emptyMessage="No detectors registered yet."
          onRowClick={(d) => {
            void navigate(`/detectors/${d.id}`);
          }}
        />
      )}
    </div>
  );
}
