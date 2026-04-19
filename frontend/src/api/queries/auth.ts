import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type User = components["schemas"]["UserRead"];

export const authKeys = {
  me: ["auth", "me"] as const,
};

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

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { email: string; password: string }) => {
      // FastAPI Users login expects application/x-www-form-urlencoded with username/password
      const body = new URLSearchParams();
      body.set("username", args.email);
      body.set("password", args.password);
      const resp = await fetch(
        `${import.meta.env.VITE_API_BASE}/auth/cookie/login`,
        { method: "POST", body, credentials: "include" },
      );
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        throw Object.assign(new Error(detail.detail ?? "Login failed"), { status: resp.status });
      }
    },
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await fetch(
        `${import.meta.env.VITE_API_BASE}/auth/cookie/logout`,
        { method: "POST", credentials: "include" },
      );
    },
    onSettled: () => {
      qc.clear();
      window.location.href = "/login";
    },
  });
}
