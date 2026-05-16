/**
 * D3.8 / R5 — merged OpenAPI types barrel.
 *
 * Call sites should import from `@/api/schema` (NOT directly from
 * `schema.gen.ts`). This file:
 *   - re-exports `paths` + `operations` from the pure codegen as-is
 *   - reconstructs `components.schemas.JobRead` with handstitched
 *     extensions intersected on
 *   - reconstructs `components.schemas.ResourceProfile` with the
 *     handstitched union members joined on
 *
 * Closes architecture.md §10 #14 fully.
 */

import type { components as Generated, operations, paths } from "./schema.gen";
import type {
  JobReadHandstitchedExtensions,
  ResourceProfileHandstitched,
} from "./schema.handstitched";

type GeneratedSchemas = Generated["schemas"];

type MergedSchemas = Omit<GeneratedSchemas, "JobRead" | "ResourceProfile"> & {
  JobRead: GeneratedSchemas["JobRead"] & JobReadHandstitchedExtensions;
  ResourceProfile:
    | GeneratedSchemas["ResourceProfile"]
    | ResourceProfileHandstitched;
};

export type components = Omit<Generated, "schemas"> & {
  schemas: MergedSchemas;
};
export type { operations, paths };
