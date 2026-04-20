import { useCurrentUser, useLogout } from "@/api/queries/auth";

export function useAuth() {
  const userQuery = useCurrentUser();
  const logoutMut = useLogout();
  return {
    currentUser: userQuery.data ?? null,
    isLoading: userQuery.isLoading,
    isUnauthenticated: userQuery.isError && (userQuery.error as { status?: number } | undefined)?.status === 401,
    logout: () => logoutMut.mutate(),
  };
}
