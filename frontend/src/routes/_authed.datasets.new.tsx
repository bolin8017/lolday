import { DatasetUploadForm } from "@/components/forms/DatasetUploadForm";

export const handle = { breadcrumb: "New dataset" };

export default function NewDatasetPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Upload dataset</h1>
      <DatasetUploadForm />
    </div>
  );
}
