import { Breadcrumb } from "./Breadcrumb";

export function TopBar() {
  return (
    <header className="flex h-14 items-center border-b bg-card px-6">
      <Breadcrumb />
    </header>
  );
}
