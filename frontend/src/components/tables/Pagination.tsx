// frontend/src/components/tables/Pagination.tsx
import { type Table as ReactTable } from "@tanstack/react-table";
import { Button } from "@/components/ui/button";

interface Props<T> {
  table: ReactTable<T>;
}

export function Pagination<T>({ table }: Props<T>) {
  const pageIndex = table.getState().pagination.pageIndex;
  const pageCount = table.getPageCount() || 1;
  return (
    <div className="flex items-center justify-end gap-2">
      <Button
        variant="outline"
        size="sm"
        onClick={() => table.previousPage()}
        disabled={!table.getCanPreviousPage()}
      >
        Prev
      </Button>
      <span className="text-sm text-muted-foreground">
        Page {pageIndex + 1} of {pageCount}
      </span>
      <Button
        variant="outline"
        size="sm"
        onClick={() => table.nextPage()}
        disabled={!table.getCanNextPage()}
      >
        Next
      </Button>
    </div>
  );
}
