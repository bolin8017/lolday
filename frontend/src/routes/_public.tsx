import { Outlet } from "react-router";

export default function PublicLayout() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-muted">
      <Outlet />
    </div>
  );
}
