import { Outlet } from "react-router";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
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

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        Loading…
      </div>
    );
  }

  if (isUnauthenticated || !currentUser) {
    return (
      <div className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 p-6 text-center">
        <h1 className="text-xl font-semibold">Session not established</h1>
        <p className="text-sm text-muted-foreground">
          Your browser reached lolday but the backend did not receive a valid Cloudflare Access
          JWT. This usually means the Cloudflare Access session expired — please re-authenticate.
        </p>
        <a
          href="/cdn-cgi/access/logout"
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          Sign in via Cloudflare Access
        </a>
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar />
        <main className="flex-1 overflow-y-auto bg-background p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
