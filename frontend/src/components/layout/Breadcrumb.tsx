import { Link } from "react-router";
import { ChevronRight } from "lucide-react";
import { useBreadcrumb } from "@/hooks/useBreadcrumb";

export function Breadcrumb() {
  const crumbs = useBreadcrumb();
  if (crumbs.length === 0) return null;
  return (
    <nav className="flex items-center text-sm text-muted-foreground">
      {crumbs.map((c, i) => (
        <span key={c.pathname} className="flex items-center">
          {i > 0 && <ChevronRight className="mx-2 h-3.5 w-3.5" />}
          {i === crumbs.length - 1 ? (
            <span className="text-foreground">{c.label}</span>
          ) : (
            <Link to={c.pathname} className="hover:text-foreground">
              {c.label}
            </Link>
          )}
        </span>
      ))}
    </nav>
  );
}
