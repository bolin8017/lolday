/**
 * D2.8 — Frontend OpenAPI snapshot contract.
 *
 * `src/api/schema.gen.ts` is generated from the backend's /openapi.json
 * via `pnpm gen-api-types`, but PR #69 hand-stitched two extension fields
 * (architecture.md §10 #14):
 *   - JobRead.detector_defaults  (override-indicator UI)
 *   - ResourceProfile.gpu1       (GPU1 form option)
 *
 * A future contributor running `pnpm gen-api-types` against a backend
 * without these fields would silently revert them, breaking the UI with
 * no compile error. This test locks the contract by asserting both
 * fields are present in a checked-in snapshot of /openapi.json.
 *
 * Regenerate the snapshot via `pnpm regen-openapi-snapshot` after backend
 * schema changes; CI fails if `git diff --exit-code` is dirty post-regen
 * (Phase 3 R5 owns wiring the CI step).
 */
import { describe, expect, it } from "vitest";

import openapiSnapshot from "../fixtures/openapi.snapshot.json";

describe("schema.gen.ts contract drift", () => {
  it("JobRead.detector_defaults is present in /openapi.json snapshot", () => {
    const schemas = (
      openapiSnapshot as unknown as {
        components: {
          schemas: Record<
            string,
            { properties?: Record<string, unknown>; enum?: unknown[] }
          >;
        };
        openapi: string;
      }
    ).components.schemas;
    const jobRead = schemas.JobRead;
    expect(jobRead).toBeDefined();
    expect(jobRead.properties).toHaveProperty("detector_defaults");
  });

  it("ResourceProfile enum includes 'gpu1'", () => {
    const schemas = (
      openapiSnapshot as unknown as {
        components: {
          schemas: Record<
            string,
            { properties?: Record<string, unknown>; enum?: unknown[] }
          >;
        };
        openapi: string;
      }
    ).components.schemas;
    const profile = schemas.ResourceProfile;
    expect(profile).toBeDefined();
    expect(profile.enum).toContain("gpu1");
  });

  it("snapshot embeds an /openapi 3.x document", () => {
    expect(
      (
        openapiSnapshot as unknown as {
          components: {
            schemas: Record<
              string,
              { properties?: Record<string, unknown>; enum?: unknown[] }
            >;
          };
          openapi: string;
        }
      ).openapi,
    ).toMatch(/^3\./);
  });
});
