import { useMatches } from "react-router";

export interface CrumbMatch {
  pathname: string;
  label: string;
}

/**
 * Routes can export a `handle.breadcrumb: (data) => string` (or a plain string).
 * `useBreadcrumb` collects the breadcrumb from every match that provides one.
 */
export function useBreadcrumb(): CrumbMatch[] {
  const matches = useMatches();
  return matches
    .filter(
      (
        m,
      ): m is typeof m & {
        handle: { breadcrumb: string | ((d: unknown) => string) };
      } =>
        Boolean(
          m.handle &&
          typeof m.handle === "object" &&
          m.handle !== null &&
          "breadcrumb" in m.handle,
        ),
    )
    .map((m) => {
      const b = (m.handle as { breadcrumb: string | ((d: unknown) => string) })
        .breadcrumb;
      return {
        pathname: m.pathname,
        label: typeof b === "function" ? b(m.data) : b,
      };
    });
}
