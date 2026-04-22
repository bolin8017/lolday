import { useQuery } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type User = components["schemas"]["UserRead"];

export const authKeys = {
  me: ["auth", "me"] as const,
};

/**
 * Phase 10.2: only /users/me remains. Login / register / logout are owned
 * by Cloudflare Access now — there is no app-level auth mutation surface.
 */
export function useCurrentUser() {
  return useQuery({
    queryKey: authKeys.me,
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/users/me");
      if (error) throw error;
      return data as User;
    },
    retry: false,
    staleTime: 5 * 60_000,
  });
}
