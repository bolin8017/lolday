import { useCurrentUser } from "@/api/queries/auth";

/**
 * There is no app-level logout. Signing out means terminating the
 * Cloudflare Access session so the next visit re-authenticates via GitHub.
 * Cloudflare exposes `/cdn-cgi/access/logout` on every Access app domain.
 */
function cloudflareLogout() {
  window.location.href = "/cdn-cgi/access/logout";
}

export function useAuth() {
  const userQuery = useCurrentUser();
  return {
    currentUser: userQuery.data ?? null,
    isLoading: userQuery.isLoading,
    isUnauthenticated:
      userQuery.isError &&
      (userQuery.error as { status?: number } | undefined)?.status === 401,
    logout: cloudflareLogout,
  };
}
