import type { ReactNode } from "react";

interface Props {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}

/**
 * Mobile-first page chrome. Stacks title above actions on `< sm` viewports;
 * lays them out side-by-side at `≥ sm`. Replaces hand-coded
 * `<div className="flex items-center justify-between"><h1>…</h1><Actions/></div>`
 * pattern that wraps poorly on phones (filter dropdown + primary button + h1
 * all on one row at 360 px is unworkable).
 */
export function PageHeader({ title, description, actions }: Props) {
  return (
    <div className="space-y-1">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold">{title}</h1>
        {actions && (
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        )}
      </div>
      {description && (
        <p className="text-sm text-muted-foreground">{description}</p>
      )}
    </div>
  );
}
