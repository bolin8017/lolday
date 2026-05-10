import { useQuery } from "@tanstack/react-query";
import { client } from "@/api/client";

export type PerGpu = {
  gpu_id: number;
  state: "lolday" | "external" | "free";
  util_percent: number;
  vram_used_mb: number;
};

export type GpuStatus = {
  total: number;
  free_count: number;
  in_use_by_lolday: number;
  in_use_by_external: number;
  fail_safe_active: boolean;
  fail_safe_reason: string | null;
  per_gpu: PerGpu[];
};
export type QueueDepth = { depth: number };
export type QueuePosition = { position: number | null };

export const clusterKeys = {
  gpu: ["cluster", "gpu-status"] as const,
  queue: ["cluster", "queue"] as const,
  jobPosition: (id: string) => ["cluster", "job-position", id] as const,
};

export function useClusterGpuStatus() {
  return useQuery({
    queryKey: clusterKeys.gpu,
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/cluster/gpu-status");
      if (error) throw error;
      return data as GpuStatus;
    },
    refetchInterval: 15_000,
    staleTime: 10_000,
  });
}

export function useClusterQueueDepth() {
  return useQuery({
    queryKey: clusterKeys.queue,
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/cluster/queue");
      if (error) throw error;
      return data as QueueDepth;
    },
    refetchInterval: 15_000,
    staleTime: 10_000,
  });
}

export function useJobQueuePosition(jobId: string, enabled: boolean = true) {
  return useQuery({
    queryKey: clusterKeys.jobPosition(jobId),
    enabled: enabled && Boolean(jobId),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/jobs/{job_id}/queue-position",
        { params: { path: { job_id: jobId } } },
      );
      if (error) throw error;
      return data as QueuePosition;
    },
    refetchInterval: 15_000,
  });
}
