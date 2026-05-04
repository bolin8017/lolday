import { type Table as ReactTable } from "@tanstack/react-table";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

interface Props<T> {
  table: ReactTable<T>;
}

export function Pagination<T>({ table }: Props<T>) {
  const { t } = useTranslation();
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
        {t("table.prev")}
      </Button>
      <span className="text-sm text-muted-foreground">
        {t("table.pageOf", { current: pageIndex + 1, total: pageCount })}
      </span>
      <Button
        variant="outline"
        size="sm"
        onClick={() => table.nextPage()}
        disabled={!table.getCanNextPage()}
      >
        {t("table.next")}
      </Button>
    </div>
  );
}
