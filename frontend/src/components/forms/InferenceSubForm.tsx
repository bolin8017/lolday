import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useRegisteredModels, useModelVersions } from "@/api/queries/models";
import { useDetectorVersion } from "@/api/queries/detectors";
import { useDatasets } from "@/api/queries/datasets";
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
  predictDatasetId: string;
  setPredictDatasetId: (v: string) => void;
  testDatasetId: string;
  setTestDatasetId: (v: string) => void;
  config: Record<string, unknown>;
  setConfig: (v: Record<string, unknown>) => void;
}

export function InferenceSubForm(p: Props) {
  const { t } = useTranslation();
  const { data: models } = useRegisteredModels();
  const { data: modelVersions } = useModelVersions(
    p.sourceModelOwner,
    p.sourceModelName,
  );
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

  // When a model version is chosen, derive detector_id + tag from the model.
  // Inference always uses the training detector_version (mainstream MLOps
  // contract — model artifact is bound to its training runtime). No override
  // path; if the training version has been retired, the backend rejects the
  // job with a clear error and the user must retrain against a current
  // detector version.
  useEffect(() => {
    if (!p.sourceModelVersionId) return;
    const mv = modelVersionsArr.find((v) => v.id === p.sourceModelVersionId);
    if (!mv) return;
    p.setDerivedDetectorId(mv.detector_id);
    p.setDerivedDetectorVersionTag(mv.detector_version_tag);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to model version selection (props mutations would loop)
  }, [p.sourceModelVersionId, modelVersionsArr.length]);

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
              <HelpHint>{t("jobs.help.source_model")}</HelpHint>
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
              <SelectTrigger aria-label="Source model">
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
              <SelectTrigger aria-label="Model version">
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
                <SelectTrigger aria-label="Test dataset">
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
