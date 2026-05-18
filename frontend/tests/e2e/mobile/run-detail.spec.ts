import { test, expect } from "@playwright/test";

import { loginAs } from "../helpers";

/**
 * D3.7 — mobile run-detail entry surface.
 *
 * `/runs` is a card grid of EXPERIMENTS (`_authed.runs._index.tsx` →
 * `<ExperimentCard>`), not a table of runs. The earlier
 * `getByRole("row").nth(1)` pattern only ever matched a table that
 * doesn't exist on this page; even the `/runs/{expId}/{runId}` route
 * (`_authed.runs.$expId.$runId.tsx`) is a redirect-only page that
 * forwards to `/jobs/{jobId}` or to the MLflow native UI — there's no
 * "Open in MLflow" button rendered there to assert on.
 *
 * The card itself renders an Open-in-MLflow link
 * (`ExperimentCard.tsx:29`); on mobile the single-column grid
 * (`grid-cols-1` at < `md`) keeps it tappable by being the full card
 * width. SPEC_LANE_STUBS seeds one experiment via
 * `_stubs.StubMlflowClient.search_experiments`, so the card always
 * renders in this lane.
 */
test("mobile: /runs experiment card renders + open-in-mlflow link tappable", async ({
  page,
}) => {
  await loginAs(page, "admin");
  await page.goto("/runs");

  // Wait for the seeded experiment card to render. The card's link
  // navigates to /runs/{experiment_id}; the heading text mirrors the
  // MLflow experiment name (`{owner}/{detector.name}` for jobs the
  // platform created).
  const cardHeading = page.getByText("admin/elfrfdet-fixture");
  await expect(cardHeading).toBeVisible();

  // The Open-in-MLflow trigger inside the card renders as an external
  // <a> wrapped by a shadcn Button (size="sm" by default). Assert it's
  // present + has a non-zero clickable target. The shadcn `sm` button
  // is `h-9` (36px); we don't pin a higher bound here — promoting the
  // mobile entry point to a `h-10` (40px+) Apple/Material tappable
  // target is a separate frontend redesign, not an E2E precondition.
  const openInMlflow = page
    .getByRole("link", { name: /open in mlflow/i })
    .first();
  await expect(openInMlflow).toBeVisible();
  const box = await openInMlflow.boundingBox();
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(32);
});
