import {
  type Table as ReactTable,
  type SortingState,
} from "@tanstack/react-table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";

interface Props<T> {
  table: ReactTable<T>;
}

export function MobileSortBar<T>({ table }: Props<T>) {
  const sortable = table.getAllColumns().filter((c) => c.getCanSort());
  if (sortable.length === 0) return null;

  const current = table.getState().sorting[0];
  const value = current ? current.id : "";

  return (
    <div className="flex items-center gap-2">
      <Label
        htmlFor="mobile-sort"
        className="shrink-0 text-xs text-muted-foreground"
      >
        Sort by
      </Label>
      <Select
        value={value}
        onValueChange={(id) => {
          const next: SortingState = id ? [{ id, desc: false }] : [];
          table.setSorting(next);
        }}
      >
        <SelectTrigger id="mobile-sort" className="h-9" aria-label="Sort by">
          <SelectValue placeholder="Default order" />
        </SelectTrigger>
        <SelectContent>
          {sortable.map((c) => {
            const header = c.columnDef.header;
            const label =
              typeof header === "string"
                ? header
                : (c.columnDef.meta?.cardLabel ?? c.id);
            return (
              <SelectItem key={c.id} value={c.id}>
                {label}
              </SelectItem>
            );
          })}
        </SelectContent>
      </Select>
    </div>
  );
}
