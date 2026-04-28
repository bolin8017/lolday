import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type RegisteredModel = components["schemas"]["RegisteredModelSummary"];
export type ModelVersion = components["schemas"]["ModelVersionRead"];
export type Stage = "None" | "Staging" | "Production" | "Archived";

export const modelsKeys = {
  all: ["models"] as const,
  list: () => [...modelsKeys.all, "list"] as const,
  detail: (name: string) => [...modelsKeys.all, "detail", name] as const,
  versions: (name: string) => [...modelsKeys.all, "versions", name] as const,
  version: (id: string) => [...modelsKeys.all, "version", id] as const,
  versionForJob: (jobId: string) => [...modelsKeys.all, "version-for-job", jobId] as const,
};

export function useRegisteredModels() {
  return useQuery({
    queryKey: modelsKeys.list(),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models");
      if (error) throw error;
      return data as RegisteredModel[];
    },
  });
}

export function useModelDetail(name: string) {
  return useQuery({
    queryKey: modelsKeys.detail(name),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models/{name}", { params: { path: { name } } });
      if (error) throw error;
      return data as RegisteredModel;
    },
    enabled: Boolean(name),
  });
}

export function useModelVersions(name: string) {
  return useQuery({
    queryKey: modelsKeys.versions(name),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models/{name}/versions", {
        params: { path: { name } },
      });
      if (error) throw error;
      return data;
    },
    enabled: Boolean(name),
  });
}

export function useModelVersion(id: string | null | undefined) {
  return useQuery({
    queryKey: modelsKeys.version(id ?? ""),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models/versions/{version_id}", {
        params: { path: { version_id: id! } },
      });
      if (error) throw error;
      return data as ModelVersion;
    },
    enabled: Boolean(id),
  });
}

export function useModelVersionForJob(jobId: string | null | undefined) {
  return useQuery({
    queryKey: modelsKeys.versionForJob(jobId ?? ""),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models/versions", {
        params: { query: { source_job_id: jobId! } },
      });
      if (error) throw error;
      const items = (data?.items ?? []) as ModelVersion[];
      return items.length > 0 ? items[0] : null;
    },
    enabled: Boolean(jobId),
  });
}

export function useTransitionModel(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { version: number; target_stage: Stage; comment?: string }) => {
      const { data, error } = await client.POST(
        "/api/v1/models/{name}/versions/{version}/transition",
        { params: { path: { name, version: args.version } }, body: { to_stage: args.target_stage, comment: args.comment } },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: modelsKeys.all }),
  });
}
