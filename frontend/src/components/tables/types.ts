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

/**
 * Slot a column occupies in the mobile card layout.
 *
 * Exported so `CardList.tsx` and `MobileSortBar.tsx` share a single source of
 * truth — adding a slot here is a TS error in every consumer that branches on
 * the value, preventing drift between the augmentation and the rendering code.
 */
export type CardSlot = "title" | "subtitle" | "body" | "actions" | "hidden";

declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- TData/TValue match TanStack's own ColumnMeta<TData, TValue> signature; required for module augmentation
  interface ColumnMeta<TData, TValue> {
    /** Label shown alongside the value in card body rows. Defaults to the column header string when absent. */
    cardLabel?: string;
    /**
     * Where to place this column in the mobile card layout:
     * - `title`: large header text at top-left
     * - `subtitle`: small muted text rendered inline after the title (status / type chips)
     * - `body`: label/value row in the card body (default for unmarked columns)
     * - `actions`: icon-button slot at top-right (e.g. row dropdown trigger)
     * - `hidden`: omit from card entirely (e.g. id columns visible only on desktop)
     *
     * @default "body"
     */
    cardSlot?: CardSlot;
    /**
     * Override the body slot ordering. Lower values render first. Defaults to
     * column array order. Applies to `body` slot only — ignored for
     * `title` / `subtitle` / `actions` / `hidden`.
     */
    cardOrder?: number;
  }
}

// `export type {}` makes TypeScript treat this file as an ES module (not a
// global ambient script), which is required for `declare module` augmentation
// to merge into the correct module scope.
export type {} from "@tanstack/react-table";
