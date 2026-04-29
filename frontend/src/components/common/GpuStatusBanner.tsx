import {
  useClusterGpuStatus,
  useClusterQueueDepth,
} from "@/api/queries/cluster";
import { Card, CardContent } from "@/components/ui/card";
import { Cpu, Loader2 } from "lucide-react";

export function GpuStatusBanner() {
  const gpu = useClusterGpuStatus();
  const queue = useClusterQueueDepth();

  return (
    <Card>
      <CardContent className="flex items-center gap-4 py-3 text-sm">
        <Cpu className="h-4 w-4 text-muted-foreground" />
        {gpu.isLoading || queue.isLoading ? (
          <span className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" /> loading cluster status…
          </span>
        ) : gpu.isError || queue.isError ? (
          <span className="text-muted-foreground">
            cluster status unavailable
          </span>
        ) : (
          <>
            <span>
              <strong>
                {gpu.data?.in_use ?? 0}/{gpu.data?.total ?? 0}
              </strong>{" "}
              GPUs allocated
              <span className="text-muted-foreground">
                {" "}
                ({gpu.data?.idle ?? 0} idle)
              </span>
            </span>
            <span className="text-muted-foreground">·</span>
            <span>
              <strong>{queue.data?.depth ?? 0}</strong> jobs queued
            </span>
          </>
        )}
      </CardContent>
    </Card>
  );
}
