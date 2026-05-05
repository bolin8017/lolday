import { useParams, Link, useNavigate } from "react-router";
import { useState } from "react";
import {
  useDetector,
  useDetectorVersions,
  useDetectorBuilds,
  useAvailableTags,
  useTriggerBuild,
  useCancelBuild,
  useDetectorVersion,
  useDeleteDetector,
  useDeleteVersion,
} from "@/api/queries/detectors";
import { DeleteConfirmDialog } from "@/components/common/DeleteConfirmDialog";
import { detailToDeleteBanner } from "@/components/common/deleteErrorBanner";
import { LoldayApiError } from "@/api/errors";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/layout/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataTable } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/common/StatusBadge";
import { LogTail } from "@/components/common/LogTail";
import { JsonTreeView } from "@/components/common/JsonTreeView";
import { formatRelative, formatDuration } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Detector" };

// Phase 13a fix: was `interface VersionRow { tag: string; ... }` but backend
// schema (VersionRead in app/schemas/detector.py) uses `git_tag`. The
// mismatch made `row.original.tag` undefined, so View manifest and
// Delete-version both did nothing. Field names below MUST match the
// backend VersionRead model. The list endpoint currently returns a dict
// (not response_model-typed), so this can't be sourced from schema.gen.ts;
// when that's fixed, replace with `components["schemas"]["VersionRead"]`.
interface VersionRow {
  id: string;
  git_tag: string;
  git_sha: string;
  status: string;
  built_at: string;
}
interface BuildRow {
  id: string;
  git_tag: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  log_tail: string | null;
}

export default function DetectorDetailPage() {
  const { id = "" } = useParams();
  const { data: det } = useDetector(id);
  const { data: versions } = useDetectorVersions(id);
  const { data: builds } = useDetectorBuilds(id);
  const { data: tags } = useAvailableTags(id);
  const triggerBuild = useTriggerBuild(id);
  const cancelBuild = useCancelBuild(id);
  const [pickedTag, setPickedTag] = useState<string | null>(null);
  const [buildDialogOpen, setBuildDialogOpen] = useState(false);
  const [openManifestTag, setOpenManifestTag] = useState<string | null>(null);

  if (!det) return <p className="text-muted-foreground">Loading…</p>;

  // Envelope-aware: { items: T[] } or T[]
  const unwrap = <T,>(payload: unknown): T[] => {
    if (Array.isArray(payload)) return payload as T[];
    if (payload && typeof payload === "object" && "items" in payload) {
      const items = (payload as { items: unknown }).items;
      return Array.isArray(items) ? (items as T[]) : [];
    }
    return [];
  };
  const versionsArr = unwrap<VersionRow>(versions);
  const buildsArr = unwrap<BuildRow>(builds);

  const versionsCols: ColumnDef<VersionRow>[] = [
    { accessorKey: "git_tag", header: "Tag", meta: { cardSlot: "title" } },
    {
      accessorKey: "git_sha",
      header: "Commit",
      cell: ({ row }) => (
        <span className="font-mono">{row.original.git_sha.slice(0, 10)}</span>
      ),
      meta: { cardLabel: "Commit", cardSlot: "body" },
    },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
      meta: { cardSlot: "subtitle" },
    },
    {
      accessorKey: "built_at",
      header: "Built",
      cell: ({ row }) => formatRelative(row.original.built_at),
      meta: { cardLabel: "Built", cardSlot: "body" },
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setOpenManifestTag(row.original.git_tag)}
          >
            View manifest
          </Button>
          <VersionDeleteButton detectorId={id} version={row.original} />
        </div>
      ),
      meta: { cardSlot: "actions" },
    },
  ];

  const buildsCols: ColumnDef<BuildRow>[] = [
    { accessorKey: "git_tag", header: "Tag", meta: { cardSlot: "title" } },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
      meta: { cardSlot: "subtitle" },
    },
    {
      accessorKey: "started_at",
      header: "Started",
      cell: ({ row }) => formatRelative(row.original.started_at),
      meta: { cardLabel: "Started", cardSlot: "body" },
    },
    {
      id: "duration",
      header: "Duration",
      cell: ({ row }) =>
        formatDuration(row.original.started_at, row.original.finished_at),
      meta: { cardLabel: "Duration", cardSlot: "body" },
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <div className="flex gap-1">
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="ghost" size="sm">
                Logs
              </Button>
            </SheetTrigger>
            <SheetContent className="w-[600px] sm:max-w-[640px]">
              <SheetHeader>
                <SheetTitle>
                  Build {row.original.id.slice(0, 8)} — logs
                </SheetTitle>
              </SheetHeader>
              <div className="mt-4">
                <LogTail text={row.original.log_tail ?? "(no output)"} />
              </div>
            </SheetContent>
          </Sheet>
          {["pending", "building", "scanning"].includes(
            row.original.status,
          ) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => cancelBuild.mutate(row.original.id)}
            >
              Cancel
            </Button>
          )}
        </div>
      ),
      meta: { cardSlot: "actions" },
    },
  ];

  return (
    <div className="space-y-4">
      <PageHeader
        title={det.display_name}
        actions={
          <>
            <DetectorDeleteButton detector={det} />
            <Link to="/detectors" className="text-sm text-muted-foreground">
              ← back
            </Link>
          </>
        }
      />

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="versions">Versions</TabsTrigger>
          <TabsTrigger value="builds">Builds</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <Card>
            <CardHeader>
              <CardTitle>Metadata</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="break-words">
                <span className="text-muted-foreground">Name:</span>{" "}
                <code>{det.name}</code>
              </div>
              <div>
                <span className="text-muted-foreground">Git URL:</span>{" "}
                <code
                  className="block max-w-full break-all"
                  title={det.git_url}
                >
                  {det.git_url}
                </code>
              </div>
              <div className="break-words">
                <span className="text-muted-foreground">Description:</span>{" "}
                {det.description ?? "—"}
              </div>
              <div>
                <span className="text-muted-foreground">Created:</span>{" "}
                {formatRelative(det.created_at)}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="versions">
          <DataTable
            data={versionsArr}
            columns={versionsCols}
            emptyMessage="No versions built yet."
          />
          <Sheet
            open={!!openManifestTag}
            onOpenChange={(o) => {
              if (!o) setOpenManifestTag(null);
            }}
          >
            <SheetContent className="w-[760px] sm:max-w-[800px] overflow-y-auto">
              <SheetHeader>
                <SheetTitle>Manifest: {openManifestTag}</SheetTitle>
              </SheetHeader>
              <div className="mt-4">
                {openManifestTag && (
                  <ManifestView detectorId={id} tag={openManifestTag} />
                )}
              </div>
            </SheetContent>
          </Sheet>
        </TabsContent>

        <TabsContent value="builds">
          <div className="mb-3 flex justify-end">
            <Dialog open={buildDialogOpen} onOpenChange={setBuildDialogOpen}>
              <DialogTrigger asChild>
                <Button>+ Trigger build</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Trigger build</DialogTitle>
                </DialogHeader>
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">
                    Pick a git tag from the repository:
                  </p>
                  <Select value={pickedTag ?? ""} onValueChange={setPickedTag}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select tag" />
                    </SelectTrigger>
                    <SelectContent>
                      {(tags ?? []).map((t) => (
                        <SelectItem key={t.name} value={t.name}>
                          {t.name} ({t.commit_sha.slice(0, 7)})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <DialogFooter>
                  <Button
                    disabled={!pickedTag}
                    onClick={async () => {
                      await triggerBuild.mutateAsync({ git_tag: pickedTag! });
                      setPickedTag(null);
                      setBuildDialogOpen(false);
                    }}
                  >
                    Build
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
          <DataTable
            data={buildsArr}
            columns={buildsCols}
            emptyMessage="No builds yet."
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ManifestView({
  detectorId,
  tag,
}: {
  detectorId: string;
  tag: string;
}) {
  const { data, isLoading, error } = useDetectorVersion(detectorId, tag);
  if (isLoading)
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (error)
    return <p className="text-sm text-destructive">Failed to load manifest.</p>;
  const manifest = data?.manifest;
  if (manifest == null) {
    return (
      <div className="space-y-2 text-sm">
        <p className="text-destructive">
          Version has no manifest (legacy build).
        </p>
        <p className="text-muted-foreground">
          Rebuild this version with maldet ≥ 1.1 to see the typed manifest.
        </p>
      </div>
    );
  }
  return <JsonTreeView value={manifest} collapsed={1} />;
}

function DetectorDeleteButton({
  detector,
}: {
  detector: { id: string; name: string };
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<ReturnType<
    typeof detailToDeleteBanner
  > | null>(null);
  const deleteMut = useDeleteDetector();
  const nav = useNavigate();

  return (
    <>
      <Button variant="destructive" size="sm" onClick={() => setOpen(true)}>
        Delete
      </Button>
      <DeleteConfirmDialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
        title={`Delete detector ${detector.name}?`}
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
            nav("/detectors");
          } catch (e) {
            // Phase 13a fix: read parseError's structuredDetail rather than
            // an unsafe cast on raw e — the cast was returning undefined for
            // 409 object-shaped detail and the in-flight banner never showed.
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

function VersionDeleteButton({
  detectorId,
  version,
}: {
  detectorId: string;
  version: { git_tag: string };
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<ReturnType<
    typeof detailToDeleteBanner
  > | null>(null);
  const deleteMut = useDeleteVersion(detectorId);

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className="text-destructive hover:bg-destructive/10 hover:text-destructive"
        onClick={() => setOpen(true)}
      >
        Delete
      </Button>
      <DeleteConfirmDialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
        title={`Delete version ${version.git_tag}?`}
        description={
          <>
            This soft-deletes only this version. The Harbor image for this tag
            will be permanently purged. Historical jobs that ran against this
            version remain visible.
          </>
        }
        confirmText={version.git_tag}
        onConfirm={async () => {
          try {
            await deleteMut.mutateAsync(version.git_tag);
            setOpen(false);
          } catch (e) {
            // Phase 13a fix: read parseError's structuredDetail rather than
            // an unsafe cast on raw e — the cast was returning undefined for
            // 409 object-shaped detail and the in-flight banner never showed.
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
