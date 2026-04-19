import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";
import { NON_TERMINAL_JOB_STATUSES } from "@/lib/status";

export type Job = components["schemas"]["JobRead"];
export type JobSummary = components["schemas"]["JobSummary"];
export type JobType = "train" | "evaluate" | "predict";
export type JobStatus = components["schemas"]["JobStatus"];

export const jobsKeys = {
  all: ["jobs"] as const,
  list: (params: Record<string, unknown>) => [...jobsKeys.all, "list", params] as const,
  detail: (id: string) => [...jobsKeys.all, "detail", id] as const,
  logs: (id: string) => [...jobsKeys.all, "logs", id] as const,
};

const isActive = (s: string | undefined) =>
  s ? (NON_TERMINAL_JOB_STATUSES as readonly string[]).includes(s) : false;

export function useJobs(params: { type?: JobType; status?: JobStatus } = {}) {
  return useQuery({
    queryKey: jobsKeys.list(params),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/jobs", { params: { query: params } });
      if (error) throw error;
      return data;
    },
    refetchInterval: 5000, // list: mild refresh for visible active jobs
  });
}

export function useJob(id: string) {
  return useQuery({
    queryKey: jobsKeys.detail(id),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/jobs/{job_id}", {
        params: { path: { job_id: id } },
      });
      if (error) throw error;
      return data as Job;
    },
    refetchInterval: (q) => (isActive((q.state.data as { data?: Job } | undefined)?.data?.status) ? 2000 : false),
  });
}

export function useJobLogs(id: string, jobStatus: string | undefined) {
  return useQuery({
    queryKey: jobsKeys.logs(id),
    queryFn: async () => {
      const resp = await fetch(`/api/v1/jobs/${id}/logs`, { credentials: "include" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.text();
    },
    refetchInterval: isActive(jobStatus) ? 2000 : false,
    enabled: Boolean(id),
  });
}

export function useSubmitJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["JobCreate"]) => {
      const { data, error } = await client.POST("/api/v1/jobs", { body });
      if (error) throw error;
      return data as Job;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: jobsKeys.all }),
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error } = await client.POST("/api/v1/jobs/{job_id}/cancel", {
        params: { path: { job_id: id } },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: jobsKeys.all }),
  });
}
