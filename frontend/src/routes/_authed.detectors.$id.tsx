import { useParams, Link } from "react-router";
import { useState } from "react";
import { useDetector, useDetectorVersion, useDetectorVersions, useDetectorBuilds, useAvailableTags, useTriggerBuild, useCancelBuild } from "@/api/queries/detectors";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DataTable } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/common/StatusBadge";
import { JsonViewer } from "@/components/common/JsonViewer";
import { LogTail } from "@/components/common/LogTail";
import { formatRelative, formatDuration } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Detector" };

interface VersionRow { tag: string; git_sha: string; status: string; built_at: string }
interface BuildRow { id: string; git_tag: string; status: string; started_at: string; finished_at: string | null; log_tail: string | null }

export default function DetectorDetailPage() {
  const { id = "" } = useParams();
  const { data: det } = useDetector(id);
  const { data: versions } = useDetectorVersions(id);
  const { data: builds } = useDetectorBuilds(id);
  const { data: tags } = useAvailableTags(id);
  const triggerBuild = useTriggerBuild(id);
  const cancelBuild = useCancelBuild(id);
  const [pickedTag, setPickedTag] = useState<string | null>(null);
  const [openSchemaTag, setOpenSchemaTag] = useState<string | null>(null);
  const [buildDialogOpen, setBuildDialogOpen] = useState(false);

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
    { accessorKey: "tag", header: "Tag" },
    { accessorKey: "git_sha", header: "Commit",
      cell: ({ row }) => <span className="font-mono">{row.original.git_sha.slice(0, 10)}</span> },
    { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { accessorKey: "built_at", header: "Built", cell: ({ row }) => formatRelative(row.original.built_at) },
    { id: "actions", header: "",
      cell: ({ row }) => (
        <Button variant="ghost" size="sm" onClick={() => setOpenSchemaTag(row.original.tag)}>
          View config schema
        </Button>
      ),
    },
  ];

  const buildsCols: ColumnDef<BuildRow>[] = [
    { accessorKey: "git_tag", header: "Tag" },
    { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { accessorKey: "started_at", header: "Started", cell: ({ row }) => formatRelative(row.original.started_at) },
    { id: "duration", header: "Duration",
      cell: ({ row }) => formatDuration(row.original.started_at, row.original.finished_at) },
    { id: "actions", header: "",
      cell: ({ row }) => (
        <div className="flex gap-1">
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="ghost" size="sm">Logs</Button>
            </SheetTrigger>
            <SheetContent className="w-[600px] sm:max-w-[640px]">
              <SheetHeader><SheetTitle>Build {row.original.id.slice(0, 8)} — logs</SheetTitle></SheetHeader>
              <div className="mt-4"><LogTail text={row.original.log_tail ?? "(no output)"} /></div>
            </SheetContent>
          </Sheet>
          {["pending", "building", "scanning"].includes(row.original.status) && (
            <Button variant="ghost" size="sm" onClick={() => cancelBuild.mutate(row.original.id)}>
              Cancel
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{det.display_name}</h1>
        <Link to="/detectors" className="text-sm text-muted-foreground">← back</Link>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="versions">Versions</TabsTrigger>
          <TabsTrigger value="builds">Builds</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <Card>
            <CardHeader><CardTitle>Metadata</CardTitle></CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div><span className="text-muted-foreground">Name:</span> <code>{det.name}</code></div>
              <div><span className="text-muted-foreground">Git URL:</span> <code>{det.git_url}</code></div>
              <div><span className="text-muted-foreground">Description:</span> {det.description ?? "—"}</div>
              <div><span className="text-muted-foreground">Created:</span> {formatRelative(det.created_at)}</div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="versions">
          <DataTable data={versionsArr} columns={versionsCols} emptyMessage="No versions built yet." />
          <Sheet open={!!openSchemaTag} onOpenChange={(o) => !o && setOpenSchemaTag(null)}>
            <SheetContent className="w-[720px] sm:max-w-[760px]">
              <SheetHeader><SheetTitle>Config schema: {openSchemaTag}</SheetTitle></SheetHeader>
              <div className="mt-4">
                <VersionSchemaView detectorId={id} tag={openSchemaTag ?? ""} />
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
                <DialogHeader><DialogTitle>Trigger build</DialogTitle></DialogHeader>
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">Pick a git tag from the repository:</p>
                  <Select value={pickedTag ?? ""} onValueChange={setPickedTag}>
                    <SelectTrigger><SelectValue placeholder="Select tag" /></SelectTrigger>
                    <SelectContent>
                      {(tags ?? []).map((t) => (
                        <SelectItem key={t.name} value={t.name}>{t.name} ({t.commit_sha.slice(0, 7)})</SelectItem>
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
          <DataTable data={buildsArr} columns={buildsCols} emptyMessage="No builds yet." />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function VersionSchemaView({ detectorId, tag }: { detectorId: string; tag: string }) {
  const { data } = useDetectorVersion(detectorId, tag);
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  return <JsonViewer value={data.config_schema} />;
}
