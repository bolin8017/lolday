import { JobSubmitForm } from "@/components/forms/JobSubmitForm";
import { GpuStatusBanner } from "@/components/common/GpuStatusBanner";
export const handle = { breadcrumb: "New job" };
export default function NewJobPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Submit job</h1>
      <GpuStatusBanner />
      <JobSubmitForm />
    </div>
  );
}
