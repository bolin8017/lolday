import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { ChevronLeft, MoreVertical } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { OwnerLabel } from "@/components/users/OwnerLabel";
import { VisibilityBadge } from "@/components/models/VisibilityBadge";
import { MarkdownView } from "@/components/common/MarkdownView";
import { ModelDescriptionEditor } from "@/components/forms/ModelDescriptionEditor";
import { ModelTagsEditor } from "@/components/forms/ModelTagsEditor";
import { OwnerTransferDialog } from "@/components/forms/OwnerTransferDialog";
import { DeleteModelDialog } from "@/components/forms/DeleteModelDialog";
import { ModelVisibilityDialog } from "@/components/forms/ModelVisibilityDialog";
import { ModelTransitionDialog } from "@/components/forms/ModelTransitionDialog";
import {
  useModelDetail,
  useModelVersions,
  useUpdateModelDescription,
  useUpdateModelTags,
  useTransferOwner,
  useDeleteModel,
  useDeleteVersion,
  useUpdateVisibility,
  type Stage,
} from "@/api/queries/models";
import { useCurrentUser } from "@/api/queries/auth";
import { toast } from "@/hooks/use-toast";
import { formatRelative } from "@/lib/date";

export const handle = { breadcrumb: "Model" };

type VersionAction = "visibility" | "transition" | "delete";

interface ActiveVersion {
  version: number;
  visibility: "public" | "private";
  currentStage: Stage;
  action: VersionAction;
}

export default function ModelDetailPage() {
  const { owner, name } = useParams<{ owner: string; name: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const detail = useModelDetail(owner!, name!);
  const versions = useModelVersions(owner!, name!);
  const me = useCurrentUser();

  const isOwnerOrAdmin = me.data?.handle === owner || me.data?.role === "admin";

  // Top-level dialog state
  const [editDesc, setEditDesc] = useState(false);
  const [editTags, setEditTags] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  // Per-version dialog state — one active at a time
  const [activeVersion, setActiveVersion] = useState<ActiveVersion | null>(
    null,
  );

  // Mutations
  const upDesc = useUpdateModelDescription();
  const upTags = useUpdateModelTags();
  const transfer = useTransferOwner();
  const del = useDeleteModel();
  const delVer = useDeleteVersion();
  const upVis = useUpdateVisibility();

  if (detail.isLoading || me.isLoading) {
    return <p className="text-muted-foreground">Loading…</p>;
  }
  if (detail.isError || !detail.data) {
    return <p className="text-destructive">Model not found.</p>;
  }
  const model = detail.data;
  const versionsList = versions.data ?? [];
  const hasExistingProd = versionsList.some(
    (v) => v.current_stage === "Production",
  );

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/models">
          <ChevronLeft className="h-4 w-4" /> Back to Models
        </Link>
      </Button>

      <header className="flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl">
            <OwnerLabel handle={owner!} />
            <span className="text-muted-foreground">/</span>
            <span className="font-bold">{name}</span>
          </h1>
          <p className="text-sm text-muted-foreground">
            Created {formatRelative(model.created_at)}
          </p>
        </div>
        {isOwnerOrAdmin && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon" aria-label="more">
                <MoreVertical className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => setEditDesc(true)}>
                {t("models.description.edit")}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setEditTags(true)}>
                {t("models.tags.edit")}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setTransferOpen(true)}>
                {t("models.transfer.title")}
              </DropdownMenuItem>
              <DropdownMenuItem
                className="text-destructive"
                onClick={() => setDeleteOpen(true)}
              >
                {t("models.delete.title")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </header>

      <section>
        <h2 className="mb-2 text-lg font-semibold">
          {t("models.description.title")}
        </h2>
        {model.description ? (
          <MarkdownView source={model.description} />
        ) : (
          <p className="text-muted-foreground">
            {t("models.description.empty")}
          </p>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold">{t("models.tags.title")}</h2>
        {model.tags && Object.keys(model.tags).length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {Object.entries(model.tags).map(([k, v]) => (
              <Badge key={k} variant="secondary">
                {k}={String(v)}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground">{t("models.tags.empty")}</p>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Versions</h2>
        {versions.isLoading ? (
          <p className="text-muted-foreground">Loading…</p>
        ) : versionsList.length === 0 ? (
          <p className="text-muted-foreground">No versions yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left">
                <th className="py-2">Version</th>
                <th className="py-2">Stage</th>
                <th className="py-2">Visibility</th>
                <th className="py-2">Run</th>
                <th className="py-2">Created</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {versionsList.map((v) => (
                <tr key={v.id} className="border-t">
                  <td className="py-2 font-medium">v{v.mlflow_version}</td>
                  <td className="py-2">
                    <Badge
                      variant={
                        v.current_stage === "Production"
                          ? "default"
                          : "secondary"
                      }
                    >
                      {v.current_stage}
                    </Badge>
                  </td>
                  <td className="py-2">
                    <VisibilityBadge visibility={v.visibility} />
                  </td>
                  <td className="py-2 font-mono text-xs">
                    {v.mlflow_run_id.slice(0, 8)}
                  </td>
                  <td className="py-2 text-muted-foreground">
                    {formatRelative(v.created_at)}
                  </td>
                  <td className="py-2">
                    {isOwnerOrAdmin && (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" aria-label="more">
                            <MoreVertical className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem
                            onClick={() =>
                              setActiveVersion({
                                version: v.mlflow_version,
                                visibility: v.visibility,
                                currentStage: v.current_stage as Stage,
                                action: "transition",
                              })
                            }
                          >
                            Transition stage…
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onClick={() =>
                              setActiveVersion({
                                version: v.mlflow_version,
                                visibility: v.visibility,
                                currentStage: v.current_stage as Stage,
                                action: "visibility",
                              })
                            }
                          >
                            {v.visibility === "public"
                              ? t("models.visibility.makePrivate")
                              : t("models.visibility.makePublic")}
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            className="text-destructive"
                            onClick={() =>
                              setActiveVersion({
                                version: v.mlflow_version,
                                visibility: v.visibility,
                                currentStage: v.current_stage as Stage,
                                action: "delete",
                              })
                            }
                          >
                            Delete version…
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Top-level dialogs */}
      <ModelDescriptionEditor
        open={editDesc}
        initialValue={model.description ?? null}
        onClose={() => setEditDesc(false)}
        onSubmit={async (description) => {
          await upDesc.mutateAsync({ owner: owner!, name: name!, description });
          setEditDesc(false);
          toast({ title: t("models.description.successToast") });
        }}
      />
      <ModelTagsEditor
        open={editTags}
        initialValue={(model.tags ?? {}) as Record<string, string>}
        onClose={() => setEditTags(false)}
        onSubmit={async (tags) => {
          await upTags.mutateAsync({ owner: owner!, name: name!, tags });
          setEditTags(false);
          toast({ title: t("models.tags.successToast") });
        }}
      />
      <OwnerTransferDialog
        open={transferOpen}
        onClose={() => setTransferOpen(false)}
        onSubmit={async (newOwner, comment) => {
          await transfer.mutateAsync({
            owner: owner!,
            name: name!,
            newOwner,
            comment,
          });
          setTransferOpen(false);
          navigate(`/models/${newOwner}/${name}`);
          toast({ title: t("models.transfer.successToast") });
        }}
      />
      <DeleteModelDialog
        open={deleteOpen}
        owner={owner!}
        name={name!}
        onClose={() => setDeleteOpen(false)}
        onConfirm={async () => {
          await del.mutateAsync({ owner: owner!, name: name! });
          setDeleteOpen(false);
          navigate("/models");
          toast({ title: t("models.delete.successToast") });
        }}
      />

      {/* Per-version dialogs — one active at a time */}
      {activeVersion?.action === "visibility" && (
        <ModelVisibilityDialog
          open={true}
          current={activeVersion.visibility}
          onClose={() => setActiveVersion(null)}
          onSubmit={async (visibility, comment) => {
            await upVis.mutateAsync({
              owner: owner!,
              name: name!,
              version: activeVersion.version,
              visibility,
              comment,
            });
            setActiveVersion(null);
            toast({ title: t("models.visibility.changedToast") });
          }}
        />
      )}
      {activeVersion?.action === "transition" && (
        <ModelTransitionDialog
          open={true}
          onClose={() => setActiveVersion(null)}
          owner={owner!}
          modelName={name!}
          version={activeVersion.version}
          currentStage={activeVersion.currentStage}
          hasExistingProd={hasExistingProd}
        />
      )}
      {activeVersion?.action === "delete" && (
        <DeleteVersionDialog
          open={true}
          owner={owner!}
          name={name!}
          version={activeVersion.version}
          onClose={() => setActiveVersion(null)}
          onConfirm={async () => {
            await delVer.mutateAsync({
              owner: owner!,
              name: name!,
              version: activeVersion.version,
            });
            setActiveVersion(null);
            toast({ title: t("models.deleteVersion.successToast") });
          }}
        />
      )}
    </div>
  );
}

// Inline DeleteVersionDialog — no type-to-confirm required (version deletion
// is reversible at the MLflow artefact level; model deletion is not).
function DeleteVersionDialog({
  open,
  owner,
  name,
  version,
  onClose,
  onConfirm,
}: {
  open: boolean;
  owner: string;
  name: string;
  version: number;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("models.deleteVersion.title")}</DialogTitle>
          <DialogDescription>
            {t("models.deleteVersion.warning", {
              fullName: `${owner}/${name}`,
              version,
            })}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
