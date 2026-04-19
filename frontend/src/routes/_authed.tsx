import { Outlet } from "react-router";

export default function AuthedLayout() {
  return (
    <div className="min-h-screen bg-background">
      <p className="p-8 text-muted-foreground">authed layout (stub)</p>
      <Outlet />
    </div>
  );
}
