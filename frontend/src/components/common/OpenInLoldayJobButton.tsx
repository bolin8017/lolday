import { Link } from "react-router";
import { Button } from "@/components/ui/button";

export function OpenInLoldayJobButton({ jobId }: { jobId: string }) {
  return (
    <Button asChild variant="outline" size="sm">
      <Link to={`/jobs/${jobId}`}>↗ Open job</Link>
    </Button>
  );
}
