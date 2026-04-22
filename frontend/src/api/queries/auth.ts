import { useQuery } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type User = components["schemas"]["UserRead"];

export const authKeys = {
  me: ["auth", "me"] as const,
};

/**
 * Only /users/me remains — login / register / logout are owned by
 * Cloudflare Access, so there is no app-level auth mutation surface.
 */
export function useCurrentUser() {
  return useQuery({
    queryKey: authKeys.me,
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/users/me");
      if (error) throw error;
      return data as User;
    },
    // Retry transient network errors but never a 401 — a 401 from the edge
    // is an infra event that the diagnostic screen handles; retrying just
    // adds latency before the user sees it.
    retry: (failureCount, err) => {
      const status = (err as { status?: number } | undefined)?.status;
      return status !== 401 && failureCount < 2;
    },
    staleTime: 5 * 60_000,
  });
}
