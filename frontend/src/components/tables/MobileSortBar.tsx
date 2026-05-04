import { useId } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  const uid = useId();
  const triggerId = `mobile-sort-${uid}`;
  // `cardSlot: "hidden"` columns (e.g. id-only columns) should not show up
  // as sort options — they are not visible on mobile cards, so sorting by
  // them produces a confusing "Sort by id" entry with no visible effect.
  const sortable = table
    .getAllColumns()
    .filter((c) => c.getCanSort() && c.columnDef.meta?.cardSlot !== "hidden");
  if (sortable.length === 0) return null;

  const current = table.getState().sorting[0];
  const value = current ? current.id : "";
  const sortByLabel = t("table.sortBy");

  return (
    <div className="flex items-center gap-2">
      <Label
        htmlFor={triggerId}
        className="shrink-0 text-xs text-muted-foreground"
      >
        {sortByLabel}
      </Label>
      <Select
        value={value}
        onValueChange={(id) => {
          const next: SortingState = id ? [{ id, desc: false }] : [];
          table.setSorting(next);
        }}
      >
        <SelectTrigger id={triggerId} className="h-9" aria-label={sortByLabel}>
          <SelectValue placeholder={t("table.defaultOrder")} />
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
