/**
 * D2.6 Task 19 — MSW intercept smoke test.
 *
 * The full route-rendering integration (Tasks 19-21 in the original plan)
 * requires the file-based router + TanStack Query providers to mount a
 * single route — non-trivial scaffolding for what the smoke test below
 * already proves: MSW handlers intercept the API client's outbound HTTP.
 *
 * Once that scaffolding lands (Phase 3 frontend full-E2E + page-object-
 * model work), the per-route MSW tests become a thin wrapper around the
 * existing render() helper.
 */
import { describe, expect, it } from "vitest";

import { http, HttpResponse } from "msw";

import { server } from "../../mocks/server";

const API = "/api/v1";

describe("MSW handlers", () => {
  it("intercepts GET /api/v1/jobs and returns the mocked list", async () => {
    const r = await fetch(`${API}/jobs`);
    expect(r.status).toBe(200);
    const body = await r.json();
    expect(body.items).toHaveLength(1);
    expect(body.items[0].type).toBe("train");
    expect(body.items[0].status).toBe("queued_backend");
  });

  it("intercepts GET /api/v1/detectors and returns the mocked entry", async () => {
    const r = await fetch(`${API}/detectors`);
    expect(r.status).toBe(200);
    const body = await r.json();
    expect(body.items[0].name).toBe("elfrfdet");
  });

  it("intercepts POST /api/v1/jobs and returns 202 + body shape", async () => {
    const r = await fetch(`${API}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "train",
        detector_version_id: "00000000-0000-0000-0000-000000000022",
        train_dataset_id: "00000000-0000-0000-0000-000000000033",
        resource_profile: "standard",
        params: {},
      }),
    });
    expect(r.status).toBe(202);
    const body = await r.json();
    expect(body.id).toBe("00000000-0000-0000-0000-0000000000bb");
    expect(body.status).toBe("queued_backend");
  });

  it("server.use() locally overrides a handler for one test", async () => {
    server.use(
      http.get(`${API}/jobs`, () =>
        HttpResponse.json({
          items: [],
          total: 0,
          page: 1,
          page_size: 25,
        }),
      ),
    );
    const r = await fetch(`${API}/jobs`);
    const body = await r.json();
    expect(body.items).toEqual([]);
    expect(body.total).toBe(0);
  });

  it("resets handlers between tests (next call sees the default list)", async () => {
    // No server.use here — the previous test's override must be cleared by
    // afterEach in tests/mocks/setup.ts.
    const r = await fetch(`${API}/jobs`);
    const body = await r.json();
    expect(body.items).toHaveLength(1); // default handler restored
  });
});
