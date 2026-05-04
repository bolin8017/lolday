import { NavLink } from "react-router";
import { useTranslation } from "react-i18next";
import {
  Boxes,
  Database,
  Play,
  FlaskConical,
  Layers,
  UserCog,
  User as UserIcon,
  LogOut,
} from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useAuth } from "@/hooks/useAuth";

const NAV_ITEMS = [
  { to: "/detectors", icon: Boxes, labelKey: "nav.detectors" },
  { to: "/datasets", icon: Database, labelKey: "nav.datasets" },
  { to: "/jobs", icon: Play, labelKey: "nav.jobs" },
  { to: "/runs", icon: FlaskConical, labelKey: "nav.runs" },
  { to: "/models", icon: Layers, labelKey: "nav.models" },
] as const;

export function AppSidebar() {
  const { t } = useTranslation();
  const { currentUser, logout } = useAuth();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="px-2 py-1.5 text-lg font-semibold text-primary">
          {t("app.name")}
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV_ITEMS.map(({ to, icon: Icon, labelKey }) => (
                <SidebarMenuItem key={to}>
                  <SidebarMenuButton asChild tooltip={t(labelKey)}>
                    <NavLink to={to}>
                      <Icon />
                      <span>{t(labelKey)}</span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
              {currentUser?.role === "admin" && (
                <SidebarMenuItem>
                  <SidebarMenuButton asChild tooltip={t("nav.admin")}>
                    <NavLink to="/admin/users">
                      <UserCog />
                      <span>{t("nav.admin")}</span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild tooltip={currentUser?.email ?? "—"}>
              <NavLink to="/profile">
                <UserIcon />
                <span className="truncate">{currentUser?.email ?? "—"}</span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={() => logout()}
              tooltip={t("nav.logout")}
            >
              <LogOut />
              <span>{t("nav.logout")}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        <p className="px-2 pt-1 text-[10px] text-muted-foreground">
          v{import.meta.env.VITE_APP_VERSION}
        </p>
      </SidebarFooter>
    </Sidebar>
  );
}
