import "@/components/tables/types"; // load ColumnMeta augmentation
import {
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useState } from "react";
import { useIsMobile } from "@/hooks/useIsMobile";
import { CardList } from "./CardList";
import { DesktopTable } from "./DesktopTable";
import { MobileSortBar } from "./MobileSortBar";
import { Pagination } from "./Pagination";

interface Props<T> {
  data: T[];
  columns: ColumnDef<T>[];
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
}

export function DataTable<T>({
  data,
  columns,
  emptyMessage = "No data.",
  onRowClick,
}: Props<T>) {
  const isMobile = useIsMobile();
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  return (
    <div className="space-y-3">
      {isMobile && <MobileSortBar table={table} />}
      {isMobile ? (
        <CardList
          table={table}
          emptyMessage={emptyMessage}
          onRowClick={onRowClick}
        />
      ) : (
        <DesktopTable
          table={table}
          emptyMessage={emptyMessage}
          onRowClick={onRowClick}
        />
      )}
      <Pagination table={table} />
    </div>
  );
}
