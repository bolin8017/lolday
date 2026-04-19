/**
 * Compute a TanStack Query `refetchInterval` value given a predicate.
 *
 * Usage:
 *   useQuery({
 *     queryKey: [...],
 *     queryFn: ...,
 *     refetchInterval: (query) =>
 *       computePollInterval(isNonTerminal(query.state.data?.data?.status), 2000),
 *   })
 */
export function computePollInterval(
  isActive: boolean | undefined,
  intervalMs: number,
): number | false {
  return isActive ? intervalMs : false;
}
