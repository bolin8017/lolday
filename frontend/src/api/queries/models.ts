import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type RegisteredModel = components["schemas"]["RegisteredModelSummary"];
export type ModelVersion = components["schemas"]["ModelVersionRead"];
export type Stage = "None" | "Staging" | "Production" | "Archived";

export const modelsKeys = {
  all: ["models"] as const,
  list: (filters?: {
    owner?: string;
    visibility?: "all" | "public" | "mine";
  }) => [...modelsKeys.all, "list", filters ?? {}] as const,
  detail: (owner: string, name: string) =>
    [...modelsKeys.all, "detail", owner, name] as const,
  versions: (owner: string, name: string) =>
    [...modelsKeys.all, "versions", owner, name] as const,
  version: (id: string) => [...modelsKeys.all, "version", id] as const,
  versionForJob: (jobId: string) =>
    [...modelsKeys.all, "version-for-job", jobId] as const,
};

export function useRegisteredModels(filters?: {
  owner?: string;
  visibility?: "all" | "public" | "mine";
}) {
  return useQuery({
    queryKey: modelsKeys.list(filters),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models", {
        params: { query: filters },
      });
      if (error) throw error;
      return (data ?? []) as RegisteredModel[];
    },
  });
}

export function useModelDetail(owner: string, name: string) {
  return useQuery({
    queryKey: modelsKeys.detail(owner, name),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/models/{owner}/{name}",
        {
          params: { path: { owner, name } },
        },
      );
      if (error) throw error;
      return data;
    },
    enabled: Boolean(owner) && Boolean(name),
  });
}

export function useModelVersions(owner: string, name: string) {
  return useQuery({
    queryKey: modelsKeys.versions(owner, name),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/models/{owner}/{name}/versions",
        {
          params: { path: { owner, name } },
        },
      );
      if (error) throw error;
      return data?.items ?? [];
    },
    enabled: Boolean(owner) && Boolean(name),
  });
}

export function useModelVersion(id: string | null | undefined) {
  return useQuery({
    queryKey: modelsKeys.version(id ?? ""),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/models/versions/{version_id}",
        {
          params: { path: { version_id: id! } },
        },
      );
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

export function useTransitionModelVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      version,
      toStage,
      comment,
    }: {
      owner: string;
      name: string;
      version: number;
      toStage: Stage;
      comment?: string | null;
    }) => {
      const { data, error } = await client.POST(
        "/api/v1/models/{owner}/{name}/versions/{version}/transition",
        {
          params: { path: { owner, name, version } },
          body: { to_stage: toStage, comment: comment ?? null },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: (_data, { owner, name }) => {
      qc.invalidateQueries({ queryKey: modelsKeys.detail(owner, name) });
      qc.invalidateQueries({ queryKey: modelsKeys.versions(owner, name) });
      qc.invalidateQueries({ queryKey: modelsKeys.list() });
    },
  });
}

export function useUpdateModelDescription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      description,
    }: {
      owner: string;
      name: string;
      description: string;
    }) => {
      const { data, error } = await client.PATCH(
        "/api/v1/models/{owner}/{name}",
        {
          params: { path: { owner, name } },
          body: { description },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: (_data, { owner, name }) => {
      qc.invalidateQueries({ queryKey: modelsKeys.detail(owner, name) });
      qc.invalidateQueries({ queryKey: modelsKeys.list() });
    },
  });
}

export function useUpdateModelTags() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      tags,
    }: {
      owner: string;
      name: string;
      tags: Record<string, string>;
    }) => {
      const { data, error } = await client.PATCH(
        "/api/v1/models/{owner}/{name}",
        {
          params: { path: { owner, name } },
          body: { tags },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: (_data, { owner, name }) => {
      qc.invalidateQueries({ queryKey: modelsKeys.detail(owner, name) });
      qc.invalidateQueries({ queryKey: modelsKeys.list() });
    },
  });
}

export function useTransferOwner() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      newOwner,
      comment,
    }: {
      owner: string;
      name: string;
      newOwner: string;
      comment: string | null;
    }) => {
      const { data, error } = await client.PATCH(
        "/api/v1/models/{owner}/{name}/owner",
        {
          params: { path: { owner, name } },
          body: { new_owner_handle: newOwner, comment },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: modelsKeys.all });
    },
  });
}

export function useDeleteModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ owner, name }: { owner: string; name: string }) => {
      const { error } = await client.DELETE("/api/v1/models/{owner}/{name}", {
        params: { path: { owner, name } },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: modelsKeys.all });
    },
  });
}

export function useDeleteVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      version,
    }: {
      owner: string;
      name: string;
      version: number;
    }) => {
      const { error } = await client.DELETE(
        "/api/v1/models/{owner}/{name}/versions/{version}",
        {
          params: { path: { owner, name, version } },
        },
      );
      if (error) throw error;
    },
    onSuccess: (_data, { owner, name }) => {
      qc.invalidateQueries({ queryKey: modelsKeys.detail(owner, name) });
      qc.invalidateQueries({ queryKey: modelsKeys.versions(owner, name) });
    },
  });
}

export function useUpdateVisibility() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      owner,
      name,
      version,
      visibility,
      comment,
    }: {
      owner: string;
      name: string;
      version: number;
      visibility: "public" | "private";
      comment: string | null;
    }) => {
      const { data, error } = await client.PATCH(
        "/api/v1/models/{owner}/{name}/versions/{version}/visibility",
        {
          params: { path: { owner, name, version } },
          body: { visibility, comment },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: (_data, { owner, name }) => {
      qc.invalidateQueries({ queryKey: modelsKeys.detail(owner, name) });
      qc.invalidateQueries({ queryKey: modelsKeys.versions(owner, name) });
    },
  });
}
