import { http, HttpResponse } from "msw";

/**
 * D2.6 Task 18 — Centrally-registered MSW handlers for vitest integration tier.
 *
 * Tests may locally override a handler via ``server.use(http.<method>(...))``.
 * E2E (playwright) does NOT use MSW — it runs against the real backend in k3d.
 *
 * Anti-flaky rule #1 (no network in tests) is enforced via the server's
 * ``onUnhandledRequest: "error"`` option in setup.ts; any new endpoint a
 * component starts calling must either be listed here or stubbed in-test.
 */
export const handlers = [
  http.get("/api/v1/users/me", () =>
    HttpResponse.json({
      id: "00000000-0000-0000-0000-000000000001",
      email: "msw@example.com",
      role: "developer",
      handle: "msw-dev",
      display_name: "MSW Dev",
    }),
  ),

  http.get("/api/v1/jobs", () =>
    HttpResponse.json({
      items: [
        {
          id: "00000000-0000-0000-0000-0000000000aa",
          type: "train",
          status: "queued_backend",
          detector_version_id: "00000000-0000-0000-0000-000000000022",
          submitted_at: "2026-05-16T10:00:00Z",
          mlflow_run_id: "run-1",
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
    }),
  ),

  http.get("/api/v1/detectors", () =>
    HttpResponse.json({
      items: [
        {
          id: "00000000-0000-0000-0000-000000000022",
          name: "elfrfdet",
          display_name: "ELF RF Detector",
          owner_id: "00000000-0000-0000-0000-000000000001",
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
    }),
  ),

  http.post("/api/v1/jobs", () =>
    HttpResponse.json(
      {
        id: "00000000-0000-0000-0000-0000000000bb",
        type: "train",
        status: "queued_backend",
        detector_version_id: "00000000-0000-0000-0000-000000000022",
        submitted_at: "2026-05-16T10:00:01Z",
      },
      { status: 202 },
    ),
  ),

  // D3 §10 #30 carry-over — extra endpoints needed by per-route MSW
  // integration tests (Task 28 routes/jobs + JobSubmitForm).
  http.get("/api/v1/datasets", () =>
    HttpResponse.json({
      items: [
        {
          id: "00000000-0000-0000-0000-000000000033",
          name: "fixture-train",
          owner_id: "00000000-0000-0000-0000-000000000001",
          visibility: "private",
          sample_count: 10,
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
    }),
  ),

  http.get("/api/v1/detector-versions/:id", ({ params }) =>
    HttpResponse.json({
      id: params.id,
      detector_id: "00000000-0000-0000-0000-000000000022",
      git_tag: "v1.0.0-fixture",
      image_digest:
        "sha256:1111111111111111111111111111111111111111111111111111111111111111",
      manifest: { framework: "lightning" },
    }),
  ),

  http.get("/api/v1/models", () =>
    HttpResponse.json({
      items: [
        {
          id: "00000000-0000-0000-0000-000000000044",
          owner_id: "00000000-0000-0000-0000-000000000001",
          namespace: "fixture",
          name: "fixture-model",
          version: 1,
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
    }),
  ),
];
