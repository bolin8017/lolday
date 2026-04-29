import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type Dataset = components["schemas"]["DatasetConfigRead"];

export const datasetsKeys = {
  all: ["datasets"] as const,
  list: (visibility: string) =>
    [...datasetsKeys.all, "list", visibility] as const,
  detail: (id: string) => [...datasetsKeys.all, "detail", id] as const,
};

export function useDatasets(visibility: "public" | "private" | "all" = "all") {
  return useQuery({
    queryKey: datasetsKeys.list(visibility),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/datasets", {
        params: {
          query: {
            visibility: visibility === "all" ? undefined : visibility,
          },
        },
      });
      if (error) throw error;
      return data;
    },
  });
}

export function useDataset(id: string) {
  return useQuery({
    queryKey: datasetsKeys.detail(id),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/datasets/{ds_id}", {
        params: { path: { ds_id: id } },
      });
      if (error) throw error;
      return data as Dataset;
    },
  });
}

export function useCreateDataset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["DatasetConfigCreate"]) => {
      const { data, error } = await client.POST("/api/v1/datasets", { body });
      if (error) throw error;
      return data as Dataset;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: datasetsKeys.all }),
  });
}

export function useDeleteDataset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await client.DELETE("/api/v1/datasets/{ds_id}", {
        params: { path: { ds_id: id } },
      });
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: datasetsKeys.all }),
  });
}
