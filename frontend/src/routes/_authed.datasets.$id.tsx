import { useParams, useNavigate } from "react-router";
import { useDataset, useDeleteDataset } from "@/api/queries/datasets";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { LabelDistribution } from "@/components/charts/LabelDistribution";
import { FamilyDistribution } from "@/components/charts/FamilyDistribution";
import { formatRelative } from "@/lib/date";

export const handle = { breadcrumb: "Dataset" };

export default function DatasetDetailPage() {
  const { id = "" } = useParams();
  const { data } = useDataset(id);
  const nav = useNavigate();
  const del = useDeleteDataset();
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{data.name}</h1>
          <p className="text-sm text-muted-foreground">{data.description ?? "—"}</p>
        </div>
        <Button
          variant="destructive"
          onClick={async () => {
            if (!confirm("Delete this dataset?")) return;
            await del.mutateAsync(id);
            nav("/datasets");
          }}
        >
          Delete
        </Button>
      </div>

      <Card>
        <CardHeader><CardTitle>Metadata</CardTitle></CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 text-sm">
          <div><span className="text-muted-foreground">Visibility:</span> <Badge>{data.visibility}</Badge></div>
          <div><span className="text-muted-foreground">Samples:</span> {data.sample_count.toLocaleString()}</div>
          <div><span className="text-muted-foreground">Size:</span> {(data.size_bytes / 1024).toFixed(1)} KB</div>
          <div><span className="text-muted-foreground">Created:</span> {formatRelative(data.created_at)}</div>
          <div className="col-span-2"><span className="text-muted-foreground">Checksum:</span> <code className="text-xs">{data.csv_checksum}</code></div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Label distribution</CardTitle></CardHeader>
          <CardContent><LabelDistribution data={data.label_distribution as Record<string, number>} /></CardContent>
        </Card>
        {data.family_distribution && (
          <Card>
            <CardHeader><CardTitle>Top 15 families</CardTitle></CardHeader>
            <CardContent><FamilyDistribution data={data.family_distribution as Record<string, number>} /></CardContent>
          </Card>
        )}
      </div>

      <div>
        <a
          href={`/api/v1/datasets/${id}/csv`}
          className="text-sm underline"
        >
          Download CSV
        </a>
      </div>
    </div>
  );
}
