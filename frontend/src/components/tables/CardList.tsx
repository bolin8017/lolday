import type { MouseEvent } from "react";
import {
  flexRender,
  type Cell,
  type Table as ReactTable,
} from "@tanstack/react-table";
import type { CardSlot } from "./types";
import { cn } from "@/lib/cn";

interface Props<T> {
  table: ReactTable<T>;
  emptyMessage: string;
  onRowClick?: (row: T) => void;
}

// Selectors used to detect "the user clicked an interactive widget inside the
// card body" — when this matches, swallow the row-level navigation. Mirrors
// the pattern used by GitHub / Linear / Vercel's mobile lists.
const INTERACTIVE_SELECTOR =
  'a, button, input, select, textarea, [role="button"], [role="combobox"], [role="menuitem"], [role="checkbox"], [role="switch"], [role="link"]';

function slotOf<T>(cell: Cell<T, unknown>): CardSlot {
  return cell.column.columnDef.meta?.cardSlot ?? "body";
}

function labelOf<T>(cell: Cell<T, unknown>): string | null {
  const meta = cell.column.columnDef.meta;
  if (meta?.cardLabel) return meta.cardLabel;
  const header = cell.column.columnDef.header;
  return typeof header === "string" ? header : null;
}

export function CardList<T>({ table, emptyMessage, onRowClick }: Props<T>) {
  const rows = table.getRowModel().rows;
  if (rows.length === 0) {
    return (
      <div className="rounded-md border p-6 text-center text-sm text-muted-foreground">
        {emptyMessage}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {rows.map((row) => {
        // Bucket cells by slot. Unknown / `hidden` cells fall through and are
        // never rendered; this is now codified rather than relying on
        // happy-accident filtering.
        const cells = row.getVisibleCells();
        const titleCell = cells.find((c) => slotOf(c) === "title");
        const subtitleCell = cells.find((c) => slotOf(c) === "subtitle");
        const actionsCell = cells.find((c) => slotOf(c) === "actions");
        const bodyCells = cells
          .filter((c) => slotOf(c) === "body")
          .sort((a, b) => {
            const ao = a.column.columnDef.meta?.cardOrder ?? 0;
            const bo = b.column.columnDef.meta?.cardOrder ?? 0;
            return ao - bo;
          });

        // Card-level click navigates, but interactive descendants (Select,
        // Button, Link, Checkbox, etc.) keep their own handlers. closest()
        // covers any slot — no per-slot stopPropagation wiring needed.
        const handleClick = onRowClick
          ? (e: MouseEvent<HTMLDivElement>) => {
              const target = e.target as HTMLElement;
              if (target.closest(INTERACTIVE_SELECTOR)) return;
              onRowClick(row.original);
            }
          : undefined;

        return (
          <div
            key={row.id}
            data-testid="card-list-row"
            onClick={handleClick}
            className={cn(
              "rounded-lg border bg-card p-3 shadow-sm",
              handleClick && "cursor-pointer transition-colors hover:bg-accent",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2 flex-wrap min-w-0">
                {titleCell && (
                  <div className="font-medium min-w-0 [overflow-wrap:anywhere]">
                    {flexRender(
                      titleCell.column.columnDef.cell,
                      titleCell.getContext(),
                    )}
                  </div>
                )}
                {subtitleCell && (
                  <div className="text-xs text-muted-foreground min-w-0 [overflow-wrap:anywhere]">
                    {flexRender(
                      subtitleCell.column.columnDef.cell,
                      subtitleCell.getContext(),
                    )}
                  </div>
                )}
              </div>
              {actionsCell && (
                // Defense-in-depth: even though the card-level handler ignores
                // clicks on interactive descendants, the actions slot
                // explicitly stops propagation so a non-interactive wrapper
                // around the action (e.g. a tooltip span) cannot accidentally
                // trigger onRowClick.
                <div className="shrink-0" onClick={(e) => e.stopPropagation()}>
                  {flexRender(
                    actionsCell.column.columnDef.cell,
                    actionsCell.getContext(),
                  )}
                </div>
              )}
            </div>
            {bodyCells.length > 0 && (
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                {bodyCells.map((cell) => {
                  const label = labelOf(cell);
                  return (
                    <div key={cell.id} className="contents">
                      <dt className="text-muted-foreground">{label ?? ""}</dt>
                      <dd className="m-0 min-w-0 text-right text-foreground [overflow-wrap:anywhere]">
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            )}
          </div>
        );
      })}
    </div>
  );
}
