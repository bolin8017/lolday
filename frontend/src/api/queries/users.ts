import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import { LoldayApiError } from "@/api/errors";
import { authKeys } from "./auth";
import type { components } from "@/api/schema.gen";

export type GitCredential = components["schemas"]["GitCredentialRead"];

export const usersKeys = {
  gitCredential: ["users", "git-credential"] as const,
};

export function useGitCredential() {
  return useQuery({
    queryKey: usersKeys.gitCredential,
    queryFn: async () => {
      try {
        const { data, error } = await client.GET("/api/v1/users/me/git-credential");
        if (error) throw error;
        return data as GitCredential;
      } catch (e) {
        if (e instanceof LoldayApiError && e.status === 404) return null; // not set
        throw e;
      }
    },
    retry: false,
  });
}

export function useSetGitCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { provider: "github"; token: string }) => {
      const { data, error } = await client.PUT("/api/v1/users/me/git-credential", { body: args });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: usersKeys.gitCredential }),
  });
}

export function useDeleteGitCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { error } = await client.DELETE("/api/v1/users/me/git-credential");
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: usersKeys.gitCredential }),
  });
}

export function useUpdatePassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: components["schemas"]["UserUpdate"]) => {
      const { data, error } = await client.PATCH("/api/v1/users/me", { body: args });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: authKeys.me }),
  });
}
