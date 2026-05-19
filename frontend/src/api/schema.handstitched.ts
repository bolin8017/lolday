/**
 * D3.8 / R5 — handstitched OpenAPI extensions (retired 2026-05-19).
 *
 * Historical context (kept for the audit trail): when this module was
 * added, the FastAPI backend did NOT surface two domain-model fields
 * via its `/openapi.json`:
 *   - `JobRead.detector_defaults` — backend-computed from manifest
 *   - `ResourceProfile` enum member `gpu1`
 * Both were validated server-side but absent from the codegen input,
 * so `schema.handstitched.ts` carried them as TypeScript intersections
 * merged in `schema.ts`.
 *
 * As of 2026-05-19, the backend `/openapi.json` declares both fields
 * natively (verified via `pnpm regen-openapi-snapshot` → snapshot
 * includes both; `pnpm gen-api-types` → `schema.gen.ts` includes both).
 * The handstitched override list is now empty; the module remains as
 * an empty-type identity passthrough so `schema.ts`'s merge stays a
 * no-op (`X & unknown ≡ X`, `X | never ≡ X`).
 *
 * Future natively-shipped extensions (if any) can re-populate
 * `JobReadHandstitchedExtensions` / `ResourceProfileHandstitched`
 * without disturbing the `schema.ts` barrel. If we never need this
 * pattern again, follow-up work can drop the module + the merger in
 * `schema.ts` and have call sites import `@/api/schema.gen` directly.
 *
 * Closes architecture.md §10 #14 — retirement step described in that
 * entry's "To retire either extension once the backend ships it
 * natively" sub-clause.
 */

/**
 * Extra fields stitched onto JobRead. Empty after the 2026-05-19
 * retirement — see module docstring. Aliased to `unknown` so the
 * intersection `JobRead & JobReadHandstitchedExtensions` in
 * `schema.ts` is identity (`Record<string, never>` is NOT identity —
 * its index signature constrains all keys to `never` and breaks
 * `Partial<JobRead>` call sites).
 */
export type JobReadHandstitchedExtensions = unknown;

/**
 * Extra ResourceProfile enum members. Empty after the 2026-05-19
 * retirement — see module docstring. Aliased to `never` so the union
 * `X | never` in `schema.ts` collapses to `X`.
 */
export type ResourceProfileHandstitched = never;
