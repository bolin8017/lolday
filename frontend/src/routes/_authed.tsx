import { Outlet } from "react-router";
import { TopBar } from "@/components/layout/TopBar";
import { AppSidebar } from "@/components/layout/AppSidebar";
import { SidebarProvider, SidebarInset } from "@/components/ui/sidebar";
import { ThemeProvider } from "@/components/ThemeProvider";
import { useAuth } from "@/hooks/useAuth";

/**
 * Users arrive here only after Cloudflare Access has validated their
 * GitHub identity. A 401 from the backend means cloudflared isn't
 * injecting the JWT (infra issue) rather than a user action, so we show
 * a diagnostic page and a one-click path back through Cloudflare Access
 * rather than a login form.
 */
export default function AuthedLayout() {
  const { currentUser, isLoading, isUnauthenticated } = useAuth();

  return (
    <ThemeProvider defaultTheme="system" storageKey="lolday-theme">
      {isLoading ? (
        <div className="flex min-h-screen items-center justify-center text-muted-foreground">
          Loading…
        </div>
      ) : isUnauthenticated || !currentUser ? (
        <div className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 p-6 text-center">
          <h1 className="text-xl font-semibold">Session not established</h1>
          <p className="text-sm text-muted-foreground">
            Your browser reached lolday but the backend did not receive a valid
            Cloudflare Access JWT. This usually means the Cloudflare Access
            session expired — please re-authenticate.
          </p>
          <a
            href="/cdn-cgi/access/logout"
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            Sign in via Cloudflare Access
          </a>
        </div>
      ) : (
        <SidebarProvider>
          <AppSidebar />
          <SidebarInset>
            <TopBar />
            <main className="flex-1 overflow-y-auto bg-background p-4 sm:p-6">
              <Outlet />
            </main>
          </SidebarInset>
        </SidebarProvider>
      )}
    </ThemeProvider>
  );
}
