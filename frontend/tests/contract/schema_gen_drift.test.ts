/**
 * D3.8 / R5 — schema.gen.ts drift contract.
 *
 * Phase 2 D2.8 shipped the snapshot side: the two formerly-handstitched
 * fields must appear in the checked-in `/openapi.json` snapshot. Phase
 * 3 D3.8 added the structural side: the two extensions live in
 * `schema.handstitched.ts` (NOT in `schema.gen.ts`) and the merged
 * `schema.ts` re-applies them.
 *
 * Retirement (2026-05-19) — the backend now declares both fields
 * natively in `/openapi.json`, so:
 *   - `schema.gen.ts` carries them on regen (assert presence).
 *   - `schema.handstitched.ts` is an empty-type identity passthrough
 *     (`Record<string, never>` and `never`) — see that file's docstring
 *     for the historical rationale.
 *   - Snapshot-side checks below still assert both fields are present
 *     in the backend OpenAPI doc, so a backend regression that removes
 *     them fails the PR loud.
 *
 * Closes architecture.md §10 #14 fully (Phase 2 D2.8 + Phase 3 D3.8 +
 * the 2026-05-19 retirement).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import openapiSnapshot from "../fixtures/openapi.snapshot.json";

const SCHEMA_GEN_PATH = resolve(__dirname, "../../src/api/schema.gen.ts");
const SCHEMA_HANDSTITCHED_PATH = resolve(
  __dirname,
  "../../src/api/schema.handstitched.ts",
);

function readFile(path: string): string {
  return readFileSync(path, "utf8");
}

type Snapshot = {
  components: {
    schemas: Record<
      string,
      { properties?: Record<string, unknown>; enum?: unknown[] }
    >;
  };
  openapi: string;
};

describe("schema.gen.ts contract drift (snapshot side)", () => {
  it("JobRead.detector_defaults is present in /openapi.json snapshot", () => {
    const schemas = (openapiSnapshot as unknown as Snapshot).components.schemas;
    expect(schemas.JobRead).toBeDefined();
    expect(schemas.JobRead.properties).toHaveProperty("detector_defaults");
  });

  it("ResourceProfile enum includes 'gpu1' in snapshot", () => {
    const schemas = (openapiSnapshot as unknown as Snapshot).components.schemas;
    expect(schemas.ResourceProfile).toBeDefined();
    expect(schemas.ResourceProfile.enum).toContain("gpu1");
  });

  it("snapshot embeds an OpenAPI 3.x document", () => {
    expect((openapiSnapshot as unknown as Snapshot).openapi).toMatch(/^3\./);
  });
});

describe("schema.gen.ts contract drift (structural side, post-retirement)", () => {
  it("schema.handstitched.ts is an empty-type identity passthrough (no live extensions)", () => {
    const text = readFile(SCHEMA_HANDSTITCHED_PATH);
    // `unknown` is the identity for `&` (X & unknown ≡ X).
    expect(text).toMatch(/JobReadHandstitchedExtensions\s*=\s*unknown/);
    // `never` is the identity for `|` (X | never ≡ X).
    expect(text).toMatch(/ResourceProfileHandstitched\s*=\s*never/);
  });

  it("schema.gen.ts carries the formerly-handstitched fields natively (backend caught up)", () => {
    const text = readFile(SCHEMA_GEN_PATH);
    expect(
      text,
      "schema.gen.ts should now expose detector_defaults via openapi-typescript output",
    ).toMatch(/detector_defaults/);
    // ResourceProfile enum line should include 'gpu1'.
    const profileLine = text
      .split("\n")
      .find((line) => line.includes("ResourceProfile:"));
    expect(profileLine ?? "").toMatch(/"gpu1"/);
  });
});
