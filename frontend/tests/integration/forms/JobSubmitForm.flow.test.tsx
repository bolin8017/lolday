/**
 * §10 #30 (D2.6 #21) — JobSubmitForm full-flow smoke.
 *
 * Phase 2 shipped the MSW handlers + smoke; full createMemoryRouter
 * integration against the file-based react-router 7 routes is deferred
 * to Phase 4 (needs a reusable test harness for the loader stack).
 * This test exercises the MSW handlers for the datasets +
 * detector-version + models endpoints the form depends on, proving
 * the network surface is fully mocked.
 */
import { describe, expect, it } from "vitest";

describe("JobSubmitForm MSW dependencies", () => {
  it("datasets handler responds", async () => {
    const resp = await fetch("/api/v1/datasets");
    expect(resp.ok).toBe(true);
    const body = await resp.json();
    expect(body.items[0].name).toBe("fixture-train");
  });

  it("detector-version handler responds", async () => {
    const resp = await fetch(
      "/api/v1/detector-versions/00000000-0000-0000-0000-000000000022",
    );
    expect(resp.ok).toBe(true);
    const body = await resp.json();
    expect(body.git_tag).toBe("v1.0.0-fixture");
  });

  it("models handler responds", async () => {
    const resp = await fetch("/api/v1/models");
    expect(resp.ok).toBe(true);
    const body = await resp.json();
    expect(body.items[0].name).toBe("fixture-model");
  });
});
