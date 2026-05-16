import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema";

export type User = components["schemas"]["UserRead"];
export type Role = User["role"];

export const adminKeys = {
  users: ["admin", "users"] as const,
};

export function useAdminUsers() {
  return useQuery({
    queryKey: adminKeys.users,
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/admin/users");
      if (error) throw error;
      return data as User[];
    },
    staleTime: 30_000,
  });
}

export function useUpdateUserRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { userId: string; role: Role }) => {
      const { data, error } = await client.PATCH(
        "/api/v1/admin/users/{user_id}",
        {
          params: { path: { user_id: args.userId } },
          body: { role: args.role },
        },
      );
      if (error) throw error;
      return data as User;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: adminKeys.users });
    },
  });
}
