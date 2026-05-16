/**
 * §10 #30 (D2.6 #20) — MSW integration smoke for the jobs list path.
 *
 * Phase 2 shipped the MSW handlers + smoke; this test exercises the
 * MSW response shape directly without booting the full route tree
 * (file-based react-router 7 routes are hard to render in isolation
 * because they assume the loader stack + outlet context). A future
 * full-route test would use createMemoryRouter once a reusable test
 * harness exists.
 */
import { describe, expect, it } from "vitest";

describe("/jobs MSW integration", () => {
  it("MSW returns the seeded job row", async () => {
    const resp = await fetch("/api/v1/jobs");
    expect(resp.ok).toBe(true);
    const body = await resp.json();
    expect(body.items).toHaveLength(1);
    expect(body.items[0].id).toMatch(/0aa$/);
    expect(body.items[0].type).toBe("train");
  });

  it("MSW POST /jobs returns 202 + new id", async () => {
    const resp = await fetch("/api/v1/jobs", { method: "POST" });
    expect(resp.status).toBe(202);
    const body = await resp.json();
    expect(body.id).toMatch(/0bb$/);
    expect(body.status).toBe("queued_backend");
  });
});
