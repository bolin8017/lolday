import { useEffect, useState } from "react";

type Resolved = "light" | "dark";

function read(): Resolved {
  if (typeof document === "undefined") return "light";
  const root = document.documentElement;
  if (root.classList.contains("dark")) return "dark";
  if (root.classList.contains("light")) return "light";
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }
  return "light";
}

/**
 * Returns the currently applied theme ("light" | "dark"), reflecting
 * what ThemeProvider has set on <html>. Subscribes to MutationObserver
 * so consumers re-render when the user toggles the theme.
 */
export function useResolvedTheme(): Resolved {
  const [resolved, setResolved] = useState<Resolved>(read);

  useEffect(() => {
    const update = () => setResolved(read());
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    const mql = window.matchMedia?.("(prefers-color-scheme: dark)");
    mql?.addEventListener?.("change", update);
    return () => {
      observer.disconnect();
      mql?.removeEventListener?.("change", update);
    };
  }, []);

  return resolved;
}
