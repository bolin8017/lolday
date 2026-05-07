import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import {
  useSubmitJob,
  useJob,
  JOB_TYPES,
  isJobType,
  type JobType,
} from "@/api/queries/jobs";
import { useDetectorVersions } from "@/api/queries/detectors";
import { useAuth } from "@/hooks/useAuth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { HelpHint } from "@/components/common/HelpHint";
import { TrainSubForm } from "./TrainSubForm";
import { InferenceSubForm } from "./InferenceSubForm";
import { StageExplainer } from "./StageExplainer";
import { StickyFormFooter } from "./StickyFormFooter";
import { requiredFieldsForType } from "./JobSubmitForm.logic";

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
  const [derivedDetectorId, setDerivedDetectorId] = useState("");
  const [derivedDetectorVersionTag, setDerivedDetectorVersionTag] =
    useState("");
  const [overrideDetectorVersion, setOverrideDetectorVersion] = useState(false);
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [priority, setPriority] = useState(0);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Need detector versions for inference submit (resolve tag → id).
  const { data: trainVersions } = useDetectorVersions(detectorId);
  const { data: derivedVersions } = useDetectorVersions(derivedDetectorId);

  useEffect(() => {
    if (!fromJob) return;
    if (isJobType(fromJob.type)) setType(fromJob.type);
    if (fromJob.train_dataset_id) setTrainDatasetId(fromJob.train_dataset_id);
    if (fromJob.test_dataset_id) setTestDatasetId(fromJob.test_dataset_id);
    if (fromJob.predict_dataset_id)
      setPredictDatasetId(fromJob.predict_dataset_id);
    if (fromJob.source_model_version_id)
      setSourceModelVersionId(fromJob.source_model_version_id);
  }, [fromJob]);

  const versionsForSubmit =
    type === "train"
      ? ((trainVersions as { items?: { id: string; git_tag: string }[] })
          ?.items ?? [])
      : ((derivedVersions as { items?: { id: string; git_tag: string }[] })
          ?.items ?? []);

  const canSubmit = (() => {
    const need = requiredFieldsForType(type);
    if (type === "train") {
      if (!detectorId || !versionTag) return false;
    } else {
      if (!sourceModelVersionId) return false;
      if (!derivedDetectorId || !derivedDetectorVersionTag) return false;
    }
    if (need.includes("train_dataset_id") && !trainDatasetId) return false;
    if (need.includes("test_dataset_id") && !testDatasetId) return false;
    if (need.includes("predict_dataset_id") && !predictDatasetId) return false;
    if (need.includes("source_model_version_id") && !sourceModelVersionId)
      return false;
    return true;
  })();

  const mut = useSubmitJob();
  const nav = useNavigate();

  async function submit() {
    setSubmitError(null);
    const tag = type === "train" ? versionTag : derivedDetectorVersionTag;
    const versionId = versionsForSubmit.find((v) => v.git_tag === tag)?.id;
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
            {JOB_TYPES.map((tt) => (
              <Button
                key={tt}
                variant={tt === type ? "default" : "outline"}
                onClick={() => setType(tt)}
                className="h-11"
              >
                {tt.charAt(0).toUpperCase() + tt.slice(1)}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      <StageExplainer type={type} />

      {type === "train" ? (
        <TrainSubForm
          detectorId={detectorId}
          setDetectorId={setDetectorId}
          versionTag={versionTag}
          setVersionTag={setVersionTag}
          trainDatasetId={trainDatasetId}
          setTrainDatasetId={setTrainDatasetId}
          testDatasetId={testDatasetId}
          setTestDatasetId={setTestDatasetId}
          config={config}
          setConfig={setConfig}
        />
      ) : (
        <InferenceSubForm
          type={type}
          sourceModelOwner={sourceModelOwner}
          setSourceModelOwner={setSourceModelOwner}
          sourceModelName={sourceModelName}
          setSourceModelName={setSourceModelName}
          sourceModelVersionId={sourceModelVersionId}
          setSourceModelVersionId={setSourceModelVersionId}
          derivedDetectorId={derivedDetectorId}
          setDerivedDetectorId={setDerivedDetectorId}
          derivedDetectorVersionTag={derivedDetectorVersionTag}
          setDerivedDetectorVersionTag={setDerivedDetectorVersionTag}
          overrideDetectorVersion={overrideDetectorVersion}
          setOverrideDetectorVersion={setOverrideDetectorVersion}
          predictDatasetId={predictDatasetId}
          setPredictDatasetId={setPredictDatasetId}
          testDatasetId={testDatasetId}
          setTestDatasetId={setTestDatasetId}
          config={config}
          setConfig={setConfig}
        />
      )}

      {isAdmin && (
        <Card>
          <CardHeader>
            <CardTitle>{t("jobs.priority.label")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1">
                <Label htmlFor="priority-input">
                  {t("jobs.priority.label")}
                </Label>
                <HelpHint popover>{t("jobs.help.priority_admin")}</HelpHint>
              </div>
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
