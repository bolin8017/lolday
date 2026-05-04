import { MutationCache, QueryClient } from "@tanstack/react-query";
import { toast } from "@/hooks/use-toast";
import { LoldayApiError } from "./errors";

/**
 * Module-level mutation augmentation for opt-out of the global error toast.
 * Forms that own their error UX end-to-end (inline field errors with no
 * top-level message) can set `meta: { suppressGlobalError: true }` either at
 * the mutation hook's definition (in `src/api/queries/*`) or at each
 * `useMutation({ meta: ... })` call site.
 */
declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: {
      suppressGlobalError?: boolean;
    };
  }
}

/**
 * Centralised mutation error handler. Catches the silent-failure pattern
 * where a callsite does `await mut.mutateAsync()` without try/catch — the
 * unhandled rejection used to swallow into React's default boundary with no
 * user-visible signal. With this handler in place every failed mutation
 * surfaces as a destructive toast unless the mutation explicitly opts out.
 */
export const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      if (mutation.meta?.suppressGlobalError) return;
      const title =
        error instanceof LoldayApiError
          ? error.message
          : error instanceof Error && error.message
            ? error.message
            : "Operation failed";
      toast({ title, variant: "destructive" });
    },
  }),
  defaultOptions: {
    queries: {
      retry: (failureCount, error: unknown) => {
        // Don't retry 401/403/404
        if (typeof error === "object" && error !== null && "status" in error) {
          const status = (error as { status: number }).status;
          if ([401, 403, 404].includes(status)) return false;
        }
        return failureCount < 2;
      },
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});
