import { useEffect } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useRegisteredModels, useModelVersions } from "@/api/queries/models";
import {
  useDetector,
  useDetectorVersion,
  useDetectorVersions,
} from "@/api/queries/detectors";
import { useDatasets } from "@/api/queries/datasets";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { HelpHint } from "@/components/common/HelpHint";
import { RjsfConfigForm } from "./RjsfConfigForm";

interface Props {
  type: "evaluate" | "predict";
  sourceModelOwner: string;
  setSourceModelOwner: (v: string) => void;
  sourceModelName: string;
  setSourceModelName: (v: string) => void;
  sourceModelVersionId: string;
  setSourceModelVersionId: (v: string) => void;
  derivedDetectorId: string;
  setDerivedDetectorId: (v: string) => void;
  derivedDetectorVersionTag: string;
  setDerivedDetectorVersionTag: (v: string) => void;
  overrideDetectorVersion: boolean;
  setOverrideDetectorVersion: (v: boolean) => void;
  predictDatasetId: string;
  setPredictDatasetId: (v: string) => void;
  testDatasetId: string;
  setTestDatasetId: (v: string) => void;
  config: Record<string, unknown>;
  setConfig: (v: Record<string, unknown>) => void;
}

// TODO(plan-task-17): replace literal Chinese strings below with i18n keys:
//   jobs.help.source_model
//   jobs.help.override_detector_version
//   jobs.inference.advanced_override
const HELP_SOURCE_MODEL =
  "已訓練好的模型；推論時會載入它的 weights。模型已綁定一個 detector，下方 Detector 區塊會自動帶入。";
const HELP_OVERRIDE_DETECTOR_VERSION =
  "預設使用模型訓練時的 detector version（保證可重現）。覆寫適用於 predict pipeline 有 bugfix、或想用較新的 evaluator 的情境。";
const ADVANCED_OVERRIDE_LABEL = "進階：覆寫 detector version";

export function InferenceSubForm(p: Props) {
  const { data: models } = useRegisteredModels();
  const { data: modelVersions } = useModelVersions(
    p.sourceModelOwner,
    p.sourceModelName,
  );
  const { data: detector } = useDetector(p.derivedDetectorId);
  const { data: detectorVersions } = useDetectorVersions(p.derivedDetectorId);
  const { data: detectorVersionDetail } = useDetectorVersion(
    p.derivedDetectorId,
    p.derivedDetectorVersionTag,
  );
  const { data: datasets } = useDatasets("all");

  const modelsArr = (models as { owner: string; name: string }[]) ?? [];
  const modelVersionsArr =
    (modelVersions as {
      id: string;
      mlflow_version: number;
      current_stage: string;
      detector_id: string;
      detector_version_tag: string;
    }[]) ?? [];
  const datasetsArr =
    (datasets as { items?: { id: string; name: string }[] })?.items ??
    (datasets as unknown as { id: string; name: string }[]) ??
    [];
  const detectorVersionsArr =
    (detectorVersions as { items?: { git_tag: string; status: string }[] })
      ?.items ?? [];

  // When a model version is chosen, derive detector_id + tag.
  useEffect(() => {
    if (!p.sourceModelVersionId) return;
    const mv = modelVersionsArr.find((v) => v.id === p.sourceModelVersionId);
    if (!mv) return;
    p.setDerivedDetectorId(mv.detector_id);
    if (!p.overrideDetectorVersion) {
      p.setDerivedDetectorVersionTag(mv.detector_version_tag);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to model version + override flag changes (props mutations would loop)
  }, [
    p.sourceModelVersionId,
    p.overrideDetectorVersion,
    modelVersionsArr.length,
  ]);

  const stages = detectorVersionDetail?.manifest?.stages as
    | Record<string, { params_schema?: object }>
    | undefined;
  const stageSchema = stages?.[p.type]?.params_schema;

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Source model</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <div className="flex items-center gap-1">
              <Label>Source model</Label>
              <HelpHint>{HELP_SOURCE_MODEL}</HelpHint>
            </div>
            <Select
              value={
                p.sourceModelOwner
                  ? `${p.sourceModelOwner}/${p.sourceModelName}`
                  : ""
              }
              onValueChange={(v) => {
                const [o, ...rest] = v.split("/");
                p.setSourceModelOwner(o ?? "");
                p.setSourceModelName(rest.join("/"));
                p.setSourceModelVersionId("");
                p.setDerivedDetectorId("");
                p.setDerivedDetectorVersionTag("");
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick model" />
              </SelectTrigger>
              <SelectContent>
                {modelsArr.map((m) => (
                  <SelectItem
                    key={`${m.owner}/${m.name}`}
                    value={`${m.owner}/${m.name}`}
                  >
                    {m.owner}/{m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Model version</Label>
            <Select
              value={p.sourceModelVersionId}
              onValueChange={p.setSourceModelVersionId}
              disabled={!p.sourceModelName}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick version" />
              </SelectTrigger>
              <SelectContent>
                {modelVersionsArr.map((mv) => (
                  <SelectItem key={mv.id} value={mv.id}>
                    v{mv.mlflow_version} ({mv.current_stage})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Detector (derived)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div>
            <span className="text-muted-foreground">Detector:</span>{" "}
            {detector
              ? (detector as { display_name: string }).display_name
              : "—"}
          </div>
          <div>
            <span className="text-muted-foreground">Version:</span>{" "}
            {p.overrideDetectorVersion ? (
              <Select
                value={p.derivedDetectorVersionTag}
                onValueChange={p.setDerivedDetectorVersionTag}
                disabled={!p.derivedDetectorId}
              >
                <SelectTrigger className="w-[200px]">
                  <SelectValue placeholder="Pick version" />
                </SelectTrigger>
                <SelectContent>
                  {detectorVersionsArr
                    .filter((v) => v.status === "active")
                    .map((v) => (
                      <SelectItem key={v.git_tag} value={v.git_tag}>
                        {v.git_tag}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            ) : (
              <code>{p.derivedDetectorVersionTag || "—"}</code>
            )}
          </div>
          <div className="flex items-center">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() =>
                p.setOverrideDetectorVersion(!p.overrideDetectorVersion)
              }
              className="px-0"
            >
              {p.overrideDetectorVersion ? (
                <ChevronDown className="h-4 w-4 mr-1" />
              ) : (
                <ChevronRight className="h-4 w-4 mr-1" />
              )}
              {ADVANCED_OVERRIDE_LABEL}
            </Button>
            <HelpHint>{HELP_OVERRIDE_DETECTOR_VERSION}</HelpHint>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Data</CardTitle>
        </CardHeader>
        <CardContent>
          {p.type === "evaluate" ? (
            <div>
              <Label>Test dataset</Label>
              <Select
                value={p.testDatasetId}
                onValueChange={p.setTestDatasetId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Pick dataset" />
                </SelectTrigger>
                <SelectContent>
                  {datasetsArr.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : (
            <div>
              <Label>Predict dataset</Label>
              <Select
                value={p.predictDatasetId}
                onValueChange={p.setPredictDatasetId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Pick dataset" />
                </SelectTrigger>
                <SelectContent>
                  {datasetsArr.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Hyperparameters</CardTitle>
        </CardHeader>
        <CardContent>
          {stageSchema ? (
            <RjsfConfigForm
              schema={stageSchema}
              value={p.config}
              onChange={p.setConfig}
            />
          ) : p.derivedDetectorVersionTag ? (
            <p className="text-sm text-destructive">
              Selected detector version has no params schema; rebuild with
              maldet ≥ 1.1.
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              Pick a model version to load its hyperparameter form.
            </p>
          )}
        </CardContent>
      </Card>
    </>
  );
}
