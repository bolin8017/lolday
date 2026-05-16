import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema";
import { NON_TERMINAL_JOB_STATUSES } from "@/lib/status";

export type Job = components["schemas"]["JobRead"];
export type JobSummary = components["schemas"]["JobSummary"];
export const JOB_TYPES = ["train", "evaluate", "predict"] as const;
export type JobType = (typeof JOB_TYPES)[number];
export function isJobType(v: unknown): v is JobType {
  return typeof v === "string" && (JOB_TYPES as readonly string[]).includes(v);
}
export type JobStatus = components["schemas"]["JobStatus"];

export const jobsKeys = {
  all: ["jobs"] as const,
  list: (params: Record<string, unknown>) =>
    [...jobsKeys.all, "list", params] as const,
  detail: (id: string) => [...jobsKeys.all, "detail", id] as const,
  logs: (id: string) => [...jobsKeys.all, "logs", id] as const,
};

const isActive = (s: string | undefined) =>
  s ? (NON_TERMINAL_JOB_STATUSES as readonly string[]).includes(s) : false;

export function useJobs(params: { type?: JobType; status?: JobStatus } = {}) {
  return useQuery({
    queryKey: jobsKeys.list(params),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/jobs", {
        params: { query: params },
      });
      if (error) throw error;
      return data;
    },
    refetchInterval: 5000, // list: mild refresh for visible active jobs
  });
}

export function useJob(id: string) {
  return useQuery({
    queryKey: jobsKeys.detail(id),
    enabled: Boolean(id),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/jobs/{job_id}", {
        params: { path: { job_id: id } },
      });
      if (error) throw error;
      return data as Job;
    },
    refetchInterval: (q) =>
      isActive((q.state.data as Job | undefined)?.status) ? 2000 : false,
  });
}

export function useJobLogs(id: string, jobStatus: string | undefined) {
  return useQuery({
    queryKey: jobsKeys.logs(id),
    queryFn: async () => {
      const resp = await fetch(`/api/v1/jobs/${id}/logs`, {
        credentials: "include",
      });
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
      const { data, error } = await client.POST(
        "/api/v1/jobs/{job_id}/cancel",
        {
          params: { path: { job_id: id } },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: jobsKeys.all }),
  });
}

/** Phase 6 (Task G.1) — admin-only priority patch. */
export function usePatchJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, priority }: { id: string; priority: number }) => {
      const { data, error } = await client.PATCH("/api/v1/jobs/{job_id}", {
        params: { path: { job_id: id } },
        body: { priority },
      });
      if (error) throw error;
      return data as Job;
    },
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: jobsKeys.detail(id) });
      qc.invalidateQueries({ queryKey: jobsKeys.all });
    },
  });
}
