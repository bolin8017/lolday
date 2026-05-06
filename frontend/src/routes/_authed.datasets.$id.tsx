import { useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useDataset } from "@/api/queries/datasets";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LabelDistribution } from "@/components/charts/LabelDistribution";
import { FamilyDistribution } from "@/components/charts/FamilyDistribution";
import { DatasetHeader } from "@/components/datasets/DatasetHeader";
import { DatasetKpiStrip } from "@/components/datasets/DatasetKpiStrip";
import { DatasetMetadataDetails } from "@/components/datasets/DatasetMetadataDetails";

export const handle = { breadcrumb: "Dataset" };

export default function DatasetDetailPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const { data } = useDataset(id);
  if (!data) return <p className="text-muted-foreground">Loading…</p>;

  const labelDist = (data.label_distribution ?? {}) as Record<string, number>;
  const familyDist = (data.family_distribution ?? {}) as Record<string, number>;

  return (
    <div className="space-y-4">
      <DatasetHeader dataset={data} />
      <DatasetKpiStrip dataset={data} />
      <Card>
        <CardHeader>
          <CardTitle>{t("datasets.detail.labelDistribution")}</CardTitle>
        </CardHeader>
        <CardContent>
          <LabelDistribution data={labelDist} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("datasets.detail.familyDistribution")}</CardTitle>
        </CardHeader>
        <CardContent>
          <FamilyDistribution data={familyDist} />
        </CardContent>
      </Card>
      <DatasetMetadataDetails dataset={data} />
    </div>
  );
}
