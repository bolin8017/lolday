// frontend/src/components/tables/types.ts
/**
 * Module augmentation for `@tanstack/react-table`'s `ColumnMeta` to carry
 * mobile card-mode rendering hints. Consumed by `DataTable.tsx`'s mobile
 * dispatch (`<CardList>` reads these to place each column into a card slot).
 *
 * The augmentation is type-safe; ColumnDef.meta is typed end-to-end with
 * autocomplete and refactor support.
 */
import "@tanstack/react-table";

declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- TData/TValue match TanStack's own ColumnMeta<TData, TValue> signature; required for module augmentation
  interface ColumnMeta<TData, TValue> {
    /** Label shown alongside the value in card body rows. Defaults to the column header string when absent. */
    cardLabel?: string;
    /**
     * Where to place this column in the mobile card layout:
     * - `title`: large header text at top-left
     * - `subtitle`: small text at top-right (status / type chips)
     * - `body`: label/value row in the card body (default for unmarked columns)
     * - `actions`: icon-button slot at top-right (e.g. row dropdown trigger)
     * - `hidden`: omit from card entirely (e.g. id columns visible only on desktop)
     */
    cardSlot?: "title" | "subtitle" | "body" | "actions" | "hidden";
    /** Override the body slot ordering. Lower values render first. Defaults to column array order. */
    cardOrder?: number;
  }
}

// Re-export ColumnDef so consumers can import everything from this file
// when they want the typed meta in scope.
export type {} from "@tanstack/react-table";
