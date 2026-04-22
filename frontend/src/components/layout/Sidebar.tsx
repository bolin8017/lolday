import { NavLink } from "react-router";
import { useTranslation } from "react-i18next";
import {
  Package, FolderOpen, Play, BarChart3, Tag, User as UserIcon, LogOut, Shield,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/hooks/useAuth";

const NAV_ITEMS = [
  { to: "/detectors", icon: Package, labelKey: "nav.detectors" },
  { to: "/datasets", icon: FolderOpen, labelKey: "nav.datasets" },
  { to: "/jobs", icon: Play, labelKey: "nav.jobs" },
  { to: "/runs", icon: BarChart3, labelKey: "nav.runs" },
  { to: "/models", icon: Tag, labelKey: "nav.models" },
] as const;

export function Sidebar() {
  const { t } = useTranslation();
  const { currentUser, logout } = useAuth();
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r bg-slate-900 text-slate-100">
      <div className="px-5 py-5 text-lg font-semibold text-amber-400">
        {t("app.name")}
      </div>
      <Separator className="bg-slate-800" />
      <nav className="flex-1 space-y-1 px-3 py-4">
        {NAV_ITEMS.map(({ to, icon: Icon, labelKey }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-slate-800 text-white"
                  : "text-slate-300 hover:bg-slate-800/60 hover:text-white",
              )
            }
          >
            <Icon className="h-4 w-4" />
            {t(labelKey)}
          </NavLink>
        ))}
        {currentUser?.role === "admin" && (
          <NavLink
            to="/admin/users"
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-slate-800 text-white"
                  : "text-slate-300 hover:bg-slate-800/60 hover:text-white",
              )
            }
          >
            <Shield className="h-4 w-4" />
            {t("nav.admin")}
          </NavLink>
        )}
      </nav>
      <Separator className="bg-slate-800" />
      <div className="px-3 py-4 space-y-2">
        <NavLink
          to="/profile"
          className={({ isActive }) =>
            cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm",
              isActive ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-800/60",
            )
          }
        >
          <UserIcon className="h-4 w-4" />
          <span className="truncate">{currentUser?.email ?? "—"}</span>
        </NavLink>
        <Button
          variant="ghost"
          className="w-full justify-start text-slate-300 hover:bg-slate-800/60 hover:text-white"
          onClick={() => logout()}
        >
          <LogOut className="mr-3 h-4 w-4" />
          {t("nav.logout")}
        </Button>
        <p className="pt-2 text-[10px] text-slate-500">
          v{import.meta.env.VITE_APP_VERSION}
        </p>
      </div>
    </aside>
  );
}
