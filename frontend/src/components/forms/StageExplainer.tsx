import { useTranslation } from "react-i18next";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { JobType } from "@/api/queries/jobs";

const REQUIRED_FIELDS: Record<JobType, string[]> = {
  train: ["train_dataset"],
  evaluate: ["source_model", "test_dataset"],
  predict: ["source_model", "predict_dataset"],
};

const OPTIONAL_FIELDS: Record<JobType, string[]> = {
  train: ["test_dataset", "hyperparameters"],
  evaluate: ["hyperparameters"],
  predict: ["hyperparameters"],
};

export function StageExplainer({ type }: { type: JobType }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardContent className="space-y-2 py-4 text-sm">
        <p className="font-medium">{t(`stage.${type}.title`)}</p>
        <p className="text-muted-foreground">
          {t(`stage.${type}.description`)}
        </p>
        <div className="flex flex-wrap gap-2 pt-2">
          {REQUIRED_FIELDS[type].map((f) => (
            <Badge key={`req-${f}`} variant="default">
              {t(`stage.field.${f}`)} ({t("stage.required")})
            </Badge>
          ))}
          {OPTIONAL_FIELDS[type].map((f) => (
            <Badge key={`opt-${f}`} variant="outline">
              {t(`stage.field.${f}`)} ({t("stage.optional")})
            </Badge>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
