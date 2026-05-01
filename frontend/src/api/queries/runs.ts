import { useQuery } from "@tanstack/react-query";
import { client } from "@/api/client";

export const runsKeys = {
  experiments: ["runs", "experiments"] as const,
  experimentsStats: ["runs", "experiments", "stats"] as const,
  experimentRuns: (expId: string) =>
    ["runs", "experiment", expId, "runs"] as const,
  run: (runId: string) => ["runs", "run", runId] as const,
  artifacts: (runId: string, path: string | null) =>
    ["runs", "run", runId, "artifacts", path ?? ""] as const,
};

export function useExperiments() {
  return useQuery({
    queryKey: runsKeys.experiments,
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/experiments");
      if (error) throw error;
      return data as {
        experiment_id: string;
        name: string;
        artifact_location?: string;
      }[];
    },
  });
}

export function useExperimentsWithStats() {
  return useQuery({
    queryKey: runsKeys.experimentsStats,
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/experiments", {
        params: { query: { include: "stats" } },
      });
      if (error) throw error;
      return data as {
        experiment_id: string;
        name: string;
        run_count: number | null;
        best_f1: number | null;
        latest_start_time: number | null;
      }[];
    },
  });
}

export function useExperimentRuns(expId: string) {
  return useQuery({
    queryKey: runsKeys.experimentRuns(expId),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/experiments/{experiment_id}/runs",
        {
          params: { path: { experiment_id: expId } },
        },
      );
      if (error) throw error;
      return data as {
        run_id: string;
        run_name?: string;
        status: string;
        start_time?: number;
        end_time?: number;
        metrics?: Record<string, number>;
        params?: Record<string, string>;
        tags?: Record<string, string>;
      }[];
    },
    enabled: Boolean(expId),
  });
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: runsKeys.run(runId),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/runs/{run_id}", {
        params: { path: { run_id: runId } },
      });
      if (error) throw error;
      return data;
    },
    enabled: Boolean(runId),
  });
}
