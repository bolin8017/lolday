import { useTranslation } from "react-i18next";
import {
  useDetectors,
  useDetectorVersion,
  useDetectorVersions,
} from "@/api/queries/detectors";
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
import { ClearableSelect } from "./ClearableSelect";
import { HelpHint } from "@/components/common/HelpHint";
import { RjsfConfigForm } from "./RjsfConfigForm";

interface Props {
  detectorId: string;
  setDetectorId: (v: string) => void;
  versionTag: string;
  setVersionTag: (v: string) => void;
  trainDatasetId: string;
  setTrainDatasetId: (v: string) => void;
  testDatasetId: string;
  setTestDatasetId: (v: string) => void;
  config: Record<string, unknown>;
  setConfig: (v: Record<string, unknown>) => void;
}

export function TrainSubForm(p: Props) {
  const { t } = useTranslation();
  const { data: detectors } = useDetectors();
  const { data: versions } = useDetectorVersions(p.detectorId);
  const { data: versionDetail } = useDetectorVersion(
    p.detectorId,
    p.versionTag,
  );
  const { data: datasets } = useDatasets("all");

  const detectorsArr =
    (detectors as { items?: { id: string; display_name: string }[] })?.items ??
    (detectors as unknown as { id: string; display_name: string }[]) ??
    [];
  const versionsArr =
    (versions as { items?: { id: string; git_tag: string; status: string }[] })
      ?.items ??
    (versions as unknown as
      | { id: string; git_tag: string; status: string }[]
      | undefined) ??
    [];
  const datasetsArr =
    (datasets as { items?: { id: string; name: string }[] })?.items ??
    (datasets as unknown as { id: string; name: string }[]) ??
    [];

  const stages = versionDetail?.manifest?.stages as
    | Record<string, { params_schema?: object }>
    | undefined;
  const stageSchema = stages?.train?.params_schema;

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Detector</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label>Detector</Label>
            <Select
              value={p.detectorId}
              onValueChange={(v) => {
                p.setDetectorId(v);
                p.setVersionTag("");
              }}
            >
              <SelectTrigger aria-label="Detector">
                <SelectValue placeholder="Pick detector" />
              </SelectTrigger>
              <SelectContent>
                {detectorsArr.map((d) => (
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
              value={p.versionTag}
              onValueChange={p.setVersionTag}
              disabled={!p.detectorId}
            >
              <SelectTrigger aria-label="Version">
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
          <div>
            <Label>Train dataset</Label>
            <Select
              value={p.trainDatasetId}
              onValueChange={p.setTrainDatasetId}
            >
              <SelectTrigger aria-label="Train dataset">
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
          <div>
            <div className="flex items-center gap-1">
              <Label>Test dataset</Label>
              <HelpHint>{t("jobs.help.test_dataset_optional")}</HelpHint>
            </div>
            <ClearableSelect
              value={p.testDatasetId}
              onValueChange={p.setTestDatasetId}
              clearable
            >
              <SelectTrigger aria-label="Test dataset (optional)">
                <SelectValue placeholder="Pick dataset (optional)" />
              </SelectTrigger>
              <SelectContent>
                {datasetsArr.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </ClearableSelect>
          </div>
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
          ) : p.versionTag ? (
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
    </>
  );
}
