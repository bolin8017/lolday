import { useQuery } from "@tanstack/react-query";
import { client } from "@/api/client";

export const modelsKeys = {
  all: ["models"] as const,
  list: () => [...modelsKeys.all, "list"] as const,
  versions: (name: string) => [...modelsKeys.all, "versions", name] as const,
};

export function useRegisteredModels() {
  return useQuery({
    queryKey: modelsKeys.list(),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models");
      if (error) throw error;
      return data as { name: string }[];
    },
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
