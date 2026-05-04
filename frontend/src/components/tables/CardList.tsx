import {
  flexRender,
  type Cell,
  type Table as ReactTable,
} from "@tanstack/react-table";
import { cn } from "@/lib/cn";

interface Props<T> {
  table: ReactTable<T>;
  emptyMessage: string;
  onRowClick?: (row: T) => void;
}

type Slot = "title" | "subtitle" | "body" | "actions" | "hidden";

function slotOf<T>(cell: Cell<T, unknown>): Slot {
  return (cell.column.columnDef.meta?.cardSlot as Slot | undefined) ?? "body";
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

        const handleClick = onRowClick
          ? () => onRowClick(row.original)
          : undefined;

        return (
          <div
            key={row.id}
            onClick={handleClick}
            className={cn(
              "rounded-lg border bg-card p-3 shadow-sm",
              handleClick && "cursor-pointer transition-colors hover:bg-accent",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2 flex-wrap min-w-0">
                {titleCell && (
                  <div className="font-medium">
                    {flexRender(
                      titleCell.column.columnDef.cell,
                      titleCell.getContext(),
                    )}
                  </div>
                )}
                {subtitleCell && (
                  <div className="text-xs text-muted-foreground">
                    {flexRender(
                      subtitleCell.column.columnDef.cell,
                      subtitleCell.getContext(),
                    )}
                  </div>
                )}
              </div>
              {actionsCell && (
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
                      <dd className="m-0 text-right text-foreground">
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
