import {
  useClusterGpuStatus,
  useClusterQueueDepth,
  type PerGpu,
} from "@/api/queries/cluster";
import { Card, CardContent } from "@/components/ui/card";
import { AlertTriangle, Cpu, Loader2 } from "lucide-react";

const STATE_BADGE: Record<PerGpu["state"], string> = {
  lolday: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  external:
    "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  free: "bg-muted text-muted-foreground",
};

const STATE_LABEL: Record<PerGpu["state"], string> = {
  lolday: "lolday",
  external: "external",
  free: "free",
};

function PerGpuChip({ gpu }: { gpu: PerGpu }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs ${STATE_BADGE[gpu.state]}`}
      data-testid={`gpu-chip-${gpu.gpu_id}`}
    >
      <strong>GPU {gpu.gpu_id}</strong>
      <span>{STATE_LABEL[gpu.state]}</span>
      {gpu.state !== "free" && (
        <span className="opacity-80">
          {gpu.util_percent.toFixed(1)}% ·{" "}
          {(gpu.vram_used_mb / 1024).toFixed(1)}GB
        </span>
      )}
    </span>
  );
}

export function GpuStatusBanner() {
  const gpu = useClusterGpuStatus();
  const queue = useClusterQueueDepth();

  if (gpu.isLoading || queue.isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" /> loading cluster status…
        </CardContent>
      </Card>
    );
  }

  if (gpu.isError || queue.isError || !gpu.data) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
          <Cpu className="h-4 w-4" /> cluster status unavailable
        </CardContent>
      </Card>
    );
  }

  const data = gpu.data;

  if (data.fail_safe_active) {
    return (
      <Card>
        <CardContent className="flex items-start gap-2 py-3 text-sm">
          <AlertTriangle className="h-4 w-4 mt-0.5 text-yellow-600" />
          <div className="space-y-1">
            <div className="font-medium">
              GPU status unavailable — scheduler in fail-safe mode
            </div>
            <div className="text-xs text-muted-foreground">
              {data.fail_safe_reason ??
                "DCGM signal unreachable; new jobs will be queued."}
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-2 py-3 text-sm">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-muted-foreground" />
          <span>
            <strong>{data.free_count}</strong> of <strong>{data.total}</strong>{" "}
            GPUs free
          </span>
          <span className="text-muted-foreground">·</span>
          <span>
            <strong>{queue.data?.depth ?? 0}</strong> jobs queued
          </span>
        </div>
        <div className="flex flex-wrap gap-1">
          {data.per_gpu.map((g) => (
            <PerGpuChip key={g.gpu_id} gpu={g} />
          ))}
        </div>
        {data.in_use_by_external > 0 && (
          <div className="flex items-start gap-2 text-xs text-orange-700 dark:text-orange-300">
            <AlertTriangle className="h-3 w-3 mt-0.5" />
            <span>
              External GPU activity detected — new lolday jobs will be queued
              until external usage releases.
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
