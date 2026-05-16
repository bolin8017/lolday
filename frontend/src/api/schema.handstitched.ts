/**
 * D3.8 / R5 — handstitched OpenAPI extensions.
 *
 * Backend's /openapi.json does NOT yet declare these two fields; they
 * exist in the application's domain model and are validated server-side
 * but not surfaced via the FastAPI OpenAPI doc (PR #69 + the 2026-04
 * `gpu1` audit-trail).
 *
 * This module is the SINGLE SOURCE OF TRUTH for the override list. Once
 * the backend ships either field natively, delete the corresponding
 * declaration here — the contract test in
 * `frontend/tests/contract/schema_gen_drift.test.ts` will catch a
 * mismatch.
 *
 * Closes architecture.md §10 #14 fully (Phase 2 D2.8 closed it
 * partially via the snapshot; Phase 3 D3.8 closes the structural side).
 */

/**
 * Extra fields stitched onto JobRead. Merged into the codegen JobRead
 * via TypeScript intersection in `schema.ts`.
 */
export interface JobReadHandstitchedExtensions {
  /** Detector Defaults — backend computes from manifest, not in OpenAPI. */
  detector_defaults?: { [key: string]: unknown } | null;
}

/**
 * Extra ResourceProfile enum members. Merged via TypeScript union in
 * `schema.ts`. The runtime backend accepts these; the OpenAPI doc
 * doesn't list them yet.
 */
export type ResourceProfileHandstitched = "gpu1";
