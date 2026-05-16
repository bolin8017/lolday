/**
 * D3.8 / R5 — schema.gen.ts drift contract.
 *
 * Phase 2 D2.8 shipped the snapshot side: the two handstitched fields
 * must appear in the checked-in /openapi.json snapshot. Phase 3 D3.8
 * adds the structural side: the two extensions live in
 * schema.handstitched.ts (NOT in schema.gen.ts) and the merged
 * schema.ts re-applies them.
 *
 * Closes architecture.md §10 #14 fully.
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

describe("schema.gen.ts contract drift (structural side)", () => {
  it("schema.handstitched.ts declares JobReadHandstitchedExtensions with detector_defaults", () => {
    const text = readFile(SCHEMA_HANDSTITCHED_PATH);
    expect(text).toMatch(/JobReadHandstitchedExtensions/);
    expect(text).toMatch(/detector_defaults/);
  });

  it("schema.handstitched.ts declares ResourceProfileHandstitched with 'gpu1'", () => {
    const text = readFile(SCHEMA_HANDSTITCHED_PATH);
    expect(text).toMatch(/ResourceProfileHandstitched/);
    expect(text).toMatch(/"gpu1"/);
  });

  it("schema.gen.ts does NOT carry the handstitched extensions (they belong in schema.handstitched.ts)", () => {
    const text = readFile(SCHEMA_GEN_PATH);
    expect(
      text,
      "schema.gen.ts must be 100% openapi-typescript output",
    ).not.toMatch(/detector_defaults/);
    const profileLine = text
      .split("\n")
      .find((line) => line.includes("ResourceProfile:"));
    expect(profileLine ?? "").not.toMatch(/"gpu1"/);
  });
});
