import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 767px)";

/**
 * Returns true when the viewport matches the mobile breakpoint
 * (`< 768px`). Subscribes to `matchMedia` change events so consumers
 * re-render across the breakpoint. Aligns with shadcn/ui's Sidebar
 * block, which uses the same threshold internally.
 */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window === "undefined"
      ? false
      : window.matchMedia(MOBILE_QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY);
    const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener("change", onChange);
    setIsMobile(mql.matches); // sync after hydration
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isMobile;
}
