import { RegisterDetectorForm } from "@/components/forms/RegisterDetectorForm";

export const handle = { breadcrumb: "New detector" };

export default function NewDetectorPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Register detector</h1>
      <RegisterDetectorForm />
    </div>
  );
}
