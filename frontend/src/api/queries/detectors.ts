import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type Detector = components["schemas"]["DetectorRead"];
export type DetectorVersion = components["schemas"]["VersionDetailRead"];
export type Build = components["schemas"]["BuildRead"];

export const detectorsKeys = {
  all: ["detectors"] as const,
  list: () => [...detectorsKeys.all, "list"] as const,
  detail: (id: string) => [...detectorsKeys.all, "detail", id] as const,
  versions: (id: string) => [...detectorsKeys.all, "versions", id] as const,
  version: (id: string, tag: string) =>
    [...detectorsKeys.all, "version", id, tag] as const,
  builds: (id: string) => [...detectorsKeys.all, "builds", id] as const,
  build: (id: string, bid: string) =>
    [...detectorsKeys.all, "build", id, bid] as const,
  availableTags: (id: string) =>
    [...detectorsKeys.all, "available-tags", id] as const,
};

export function useDetectors() {
  return useQuery({
    queryKey: detectorsKeys.list(),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/detectors");
      if (error) throw error;
      return data;
    },
  });
}

export function useDetector(id: string) {
  return useQuery({
    queryKey: detectorsKeys.detail(id),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/detectors/{detector_id}",
        {
          params: { path: { detector_id: id } },
        },
      );
      if (error) throw error;
      return data as Detector;
    },
  });
}

export function useDetectorVersions(id: string) {
  return useQuery({
    queryKey: detectorsKeys.versions(id),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/detectors/{detector_id}/versions",
        {
          params: { path: { detector_id: id } },
        },
      );
      if (error) throw error;
      return data;
    },
  });
}

export function useDetectorVersion(id: string, tag: string) {
  return useQuery({
    queryKey: detectorsKeys.version(id, tag),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/detectors/{detector_id}/versions/{tag}",
        {
          params: { path: { detector_id: id, tag } },
        },
      );
      if (error) throw error;
      return data as DetectorVersion;
    },
    enabled: Boolean(id && tag),
  });
}

export function useDetectorBuilds(id: string) {
  return useQuery({
    queryKey: detectorsKeys.builds(id),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/detectors/{detector_id}/builds",
        {
          params: { path: { detector_id: id } },
        },
      );
      if (error) throw error;
      return data;
    },
    refetchInterval: (q) => {
      const builds = (q.state.data as { data?: Build[] } | undefined)?.data;
      if (!builds) return false;
      const anyActive = builds.some((b) =>
        ["pending", "building", "scanning"].includes(b.status),
      );
      return anyActive ? 2000 : false;
    },
  });
}

export type AvailableTag = components["schemas"]["AvailableTag"];

export function useAvailableTags(id: string) {
  return useQuery({
    queryKey: detectorsKeys.availableTags(id),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/detectors/{detector_id}/available-tags",
        {
          params: { path: { detector_id: id } },
        },
      );
      if (error) throw error;
      return data as AvailableTag[];
    },
    enabled: Boolean(id),
  });
}

export function useRegisterDetector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["DetectorCreate"]) => {
      const { data, error } = await client.POST("/api/v1/detectors", { body });
      if (error) throw error;
      return data as Detector;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: detectorsKeys.all }),
  });
}

export function useTriggerBuild(detectorId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { git_tag: string }) => {
      const { data, error } = await client.POST(
        "/api/v1/detectors/{detector_id}/builds",
        {
          params: { path: { detector_id: detectorId } },
          body,
        },
      );
      if (error) throw error;
      return data as Build;
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: detectorsKeys.builds(detectorId) }),
  });
}

export function useCancelBuild(detectorId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (buildId: string) => {
      const { data, error } = await client.POST(
        "/api/v1/detectors/{detector_id}/builds/{build_id}/cancel",
        { params: { path: { detector_id: detectorId, build_id: buildId } } },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: detectorsKeys.builds(detectorId) }),
  });
}

export function useDeleteDetector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await client.DELETE("/api/v1/detectors/{detector_id}", {
        params: { path: { detector_id: id } },
      });
      if (error) throw error;
    },
    onSuccess: (_data, id) => {
      // Phase 13a A4: invalidate list and the deleted detector's detail
      qc.invalidateQueries({ queryKey: detectorsKeys.list() });
      qc.invalidateQueries({ queryKey: detectorsKeys.detail(id) });
    },
  });
}

export function useDeleteVersion(detectorId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (tag: string) => {
      const { error } = await client.DELETE(
        "/api/v1/detectors/{detector_id}/versions/{tag}",
        { params: { path: { detector_id: detectorId, tag } } },
      );
      if (error) throw error;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: detectorsKeys.versions(detectorId) });
      qc.invalidateQueries({ queryKey: detectorsKeys.builds(detectorId) });
    },
  });
}
