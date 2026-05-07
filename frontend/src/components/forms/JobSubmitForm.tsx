import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import {
  useDetectors,
  useDetectorVersion,
  useDetectorVersions,
} from "@/api/queries/detectors";
import { useDatasets } from "@/api/queries/datasets";
import { useRegisteredModels, useModelVersions } from "@/api/queries/models";
import {
  useSubmitJob,
  useJob,
  JOB_TYPES,
  isJobType,
  type JobType,
} from "@/api/queries/jobs";
import { useAuth } from "@/hooks/useAuth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { requiredFieldsForType } from "./JobSubmitForm.logic";
import { RjsfConfigForm } from "./RjsfConfigForm";
import { StageExplainer } from "./StageExplainer";
import { StickyFormFooter } from "./StickyFormFooter";

export function JobSubmitForm() {
  const { t } = useTranslation();
  const { currentUser } = useAuth();
  const isAdmin = currentUser?.role === "admin";

  const [params] = useSearchParams();
  const fromJobId = params.get("from");
  const { data: fromJob } = useJob(fromJobId ?? "");

  const [type, setType] = useState<JobType>("train");
  const [detectorId, setDetectorId] = useState("");
  const [versionTag, setVersionTag] = useState("");
  const [trainDatasetId, setTrainDatasetId] = useState("");
  const [testDatasetId, setTestDatasetId] = useState("");
  const [predictDatasetId, setPredictDatasetId] = useState("");
  const [sourceModelOwner, setSourceModelOwner] = useState("");
  const [sourceModelName, setSourceModelName] = useState("");
  const [sourceModelVersionId, setSourceModelVersionId] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [priority, setPriority] = useState(0);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const { data: detectors } = useDetectors();
  const { data: versions } = useDetectorVersions(detectorId);
  const { data: versionDetail } = useDetectorVersion(detectorId, versionTag);
  // manifest is JSONB on the backend, exposed as `{ [key: string]: unknown }`.
  // The shape inside is the maldet 1.1 manifest with `stages.{stage}.params_schema`.
  const stages = versionDetail?.manifest?.stages as
    | Record<string, { params_schema?: object }>
    | undefined;
  const stageSchema = stages?.[type]?.params_schema;
  const { data: datasets } = useDatasets("all");
  const { data: models } = useRegisteredModels();
  const { data: modelVersions } = useModelVersions(
    sourceModelOwner,
    sourceModelName,
  );

  // Prefill from previous job via ?from=
  useEffect(() => {
    if (!fromJob) return;
    if (isJobType(fromJob.type)) setType(fromJob.type);
    if (fromJob.train_dataset_id) setTrainDatasetId(fromJob.train_dataset_id);
    if (fromJob.test_dataset_id) setTestDatasetId(fromJob.test_dataset_id);
    if (fromJob.predict_dataset_id)
      setPredictDatasetId(fromJob.predict_dataset_id);
  }, [fromJob]);

  const datasetsArr =
    (datasets as { items?: { id: string; name: string }[] })?.items ??
    (datasets as unknown as { id: string; name: string }[]) ??
    [];
  const versionsArr =
    (versions as { items?: { id: string; git_tag: string; status: string }[] })
      ?.items ??
    (versions as unknown as
      | { id: string; git_tag: string; status: string }[]
      | undefined) ??
    [];
  const modelsArr =
    (models as { owner: string; name: string }[] | undefined) ?? [];
  const modelVersionsArr =
    (
      modelVersions as {
        items?: { id: string; mlflow_version: number; current_stage: string }[];
      }
    )?.items ?? [];

  const mut = useSubmitJob();
  const nav = useNavigate();

  const canSubmit = (() => {
    if (!detectorId || !versionTag) return false;
    const need = requiredFieldsForType(type);
    if (need.includes("train_dataset_id") && !trainDatasetId) return false;
    if (need.includes("test_dataset_id") && !testDatasetId) return false;
    if (need.includes("predict_dataset_id") && !predictDatasetId) return false;
    if (need.includes("source_model_version_id") && !sourceModelVersionId)
      return false;
    return true;
  })();

  async function submit() {
    setSubmitError(null);
    const versionId = versionsArr.find((v) => v.git_tag === versionTag)?.id;
    if (!versionId) return;
    try {
      const job = await mut.mutateAsync({
        type,
        detector_version_id: versionId,
        train_dataset_id: type === "train" ? trainDatasetId : null,
        test_dataset_id: ["train", "evaluate"].includes(type)
          ? testDatasetId
          : null,
        predict_dataset_id: type === "predict" ? predictDatasetId : null,
        source_model_version_id: ["evaluate", "predict"].includes(type)
          ? sourceModelVersionId
          : null,
        params: config,
        ...(isAdmin && priority !== 0 ? { priority } : {}),
      } as unknown as import("@/api/schema.gen").components["schemas"]["JobCreate"]);
      nav(`/jobs/${job.id}`);
    } catch (e) {
      setSubmitError((e as { detail?: string }).detail ?? "Submit failed");
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <Card>
        <CardHeader>
          <CardTitle>Job type</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            {JOB_TYPES.map((t) => (
              <Button
                key={t}
                variant={t === type ? "default" : "outline"}
                onClick={() => setType(t)}
                className="h-11"
              >
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      <StageExplainer type={type} />

      <Card>
        <CardHeader>
          <CardTitle>Detector</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label>Detector</Label>
            <Select
              value={detectorId}
              onValueChange={(v) => {
                setDetectorId(v);
                setVersionTag("");
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick detector" />
              </SelectTrigger>
              <SelectContent>
                {(
                  (
                    detectors as {
                      items?: { id: string; display_name: string }[];
                    }
                  )?.items ??
                  (detectors as unknown as {
                    id: string;
                    display_name: string;
                  }[]) ??
                  []
                ).map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Version</Label>
            <Select
              value={versionTag}
              onValueChange={setVersionTag}
              disabled={!detectorId}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick version" />
              </SelectTrigger>
              <SelectContent>
                {versionsArr
                  .filter((v) => v.status === "active")
                  .map((v) => (
                    <SelectItem key={v.git_tag} value={v.git_tag}>
                      {v.git_tag}
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
        <CardContent className="space-y-3">
          {type === "train" && (
            <>
              <DatasetField
                label="Train dataset"
                value={trainDatasetId}
                onChange={setTrainDatasetId}
                options={datasetsArr}
              />
              <DatasetField
                label="Test dataset"
                value={testDatasetId}
                onChange={setTestDatasetId}
                options={datasetsArr}
              />
            </>
          )}
          {type === "evaluate" && (
            <DatasetField
              label="Test dataset"
              value={testDatasetId}
              onChange={setTestDatasetId}
              options={datasetsArr}
            />
          )}
          {type === "predict" && (
            <DatasetField
              label="Predict dataset"
              value={predictDatasetId}
              onChange={setPredictDatasetId}
              options={datasetsArr}
            />
          )}
          {["evaluate", "predict"].includes(type) && (
            <>
              <div>
                <Label>Source model</Label>
                <Select
                  value={
                    sourceModelOwner
                      ? `${sourceModelOwner}/${sourceModelName}`
                      : ""
                  }
                  onValueChange={(v) => {
                    const [o, ...rest] = v.split("/");
                    setSourceModelOwner(o ?? "");
                    setSourceModelName(rest.join("/"));
                    setSourceModelVersionId("");
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
                  value={sourceModelVersionId}
                  onValueChange={setSourceModelVersionId}
                  disabled={!sourceModelName}
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
            </>
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
              value={config}
              onChange={setConfig}
            />
          ) : versionTag ? (
            <p className="text-sm text-destructive">
              Selected detector version has no params schema; rebuild with
              maldet ≥ 1.1.
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">
              Pick a detector + version to load its hyperparameter form.
            </p>
          )}
        </CardContent>
      </Card>

      {isAdmin && (
        <Card>
          <CardHeader>
            <CardTitle>{t("jobs.priority.label")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex items-center gap-3">
              <Label htmlFor="priority-input">{t("jobs.priority.label")}</Label>
              <Input
                id="priority-input"
                type="number"
                min={0}
                step={1}
                className="w-24"
                value={priority}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  setPriority(isNaN(v) || v < 0 ? 0 : v);
                }}
              />
            </div>
            {priority > 0 && (
              <p
                className="text-sm rounded-md border border-amber-400/60 bg-amber-50 px-3 py-2 text-amber-900 dark:bg-amber-900/20 dark:text-amber-300"
                role="alert"
              >
                {t("jobs.priority.warning")}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {submitError && <p className="text-sm text-destructive">{submitError}</p>}
      <StickyFormFooter>
        <Button variant="ghost" onClick={() => nav(-1)} className="h-11">
          Cancel
        </Button>
        <Button
          disabled={!canSubmit || mut.isPending}
          onClick={submit}
          className="h-11"
        >
          Submit job
        </Button>
      </StickyFormFooter>
    </div>
  );
}

function DatasetField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { id: string; name: string }[];
}) {
  return (
    <div>
      <Label>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger>
          <SelectValue placeholder="Pick dataset" />
        </SelectTrigger>
        <SelectContent>
          {options.map((d) => (
            <SelectItem key={d.id} value={d.id}>
              {d.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
