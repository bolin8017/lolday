import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { JobSummary, JobStatus } from "@/api/queries/jobs";

const { patchState, mutateMock } = vi.hoisted(() => ({
  patchState: { isPending: false, isError: false, error: null as unknown },
  mutateMock: vi.fn(),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

vi.mock("@/api/queries/jobs", async () => {
  // Use importActual so the JobSummary / JobStatus type re-exports stay
  // available — type info is erased at runtime, no runtime cost.
  const actual =
    await vi.importActual<typeof import("@/api/queries/jobs")>(
      "@/api/queries/jobs",
    );
  return {
    ...actual,
    usePatchJob: () => ({
      mutate: mutateMock,
      isPending: patchState.isPending,
      isError: patchState.isError,
      error: patchState.error,
    }),
  };
});

import { PriorityCell } from "@/routes/_authed.jobs._index";

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "job-x",
    type: "train",
    status: "queued_backend",
    detector_version_id: "dv-1",
    owner_id: "u-1",
    mlflow_run_id: null,
    k8s_job_name: null,
    failure_reason: null,
    priority: 0,
    submitted_at: "2026-05-19T00:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  patchState.isPending = false;
  patchState.isError = false;
  patchState.error = null;
  mutateMock.mockReset();
});

describe("_authed.jobs._index.tsx (PriorityCell)", () => {
  it("for status='queued_backend' (canEdit), renders the popover trigger with a11y label", () => {
    const { getByRole } = render(<PriorityCell job={makeJob()} />);
    // aria-label is t('jobs.priority.label') — under the identity t()
    // mock this is the literal i18n key.
    const trigger = getByRole("button", { name: "jobs.priority.label" });
    expect(trigger).toBeInTheDocument();
  });

  it("for status='queued_backend' priority=0 → renders the 'normal' PriorityBadge", () => {
    const { getByText, queryByText } = render(
      <PriorityCell job={makeJob({ priority: 0 })} />,
    );
    expect(getByText("jobs.priority.normal")).toBeInTheDocument();
    expect(queryByText(/jobs.priority.high/)).toBeNull();
  });

  it("for status='queued_backend' priority=1 → renders the 'high' PriorityBadge", () => {
    const { getByText } = render(
      <PriorityCell job={makeJob({ priority: 1 })} />,
    );
    expect(getByText(/jobs.priority.high/)).toBeInTheDocument();
  });

  it("treats any priority > 0 as the 'high' bucket (current normalisation)", () => {
    // Source: `current: 0 | 1 = (job.priority ?? 0) === 0 ? 0 : 1` —
    // pin so a refactor doesn't accidentally introduce a 3-state ladder
    // (the backend supports integer priority but the cell collapses it
    // to a binary toggle for the admin UI).
    const { getByText } = render(
      <PriorityCell job={makeJob({ priority: 7 })} />,
    );
    expect(getByText(/jobs.priority.high/)).toBeInTheDocument();
  });

  it("treats an undefined priority as the 'normal' bucket", () => {
    // Source: `(job.priority ?? 0) === 0 ? 0 : 1` — the `??` covers
    // missing priority on older backend responses. Pin so the default
    // remains 'normal' instead of crashing on undefined.
    const job = makeJob();
    // `priority` is `number | undefined` in the openapi-typescript shape;
    // delete the key to exercise the `??` fallback path.
    delete (job as { priority?: number }).priority;
    const { getByText } = render(<PriorityCell job={job} />);
    expect(getByText("jobs.priority.normal")).toBeInTheDocument();
  });

  it.each<JobStatus>([
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timeout",
  ])(
    "for status='%s' (terminal or running), renders '—' instead of the badge / trigger",
    (status) => {
      // Once a job has started running its priority is no longer
      // actionable — the dash carries that signal to the admin so they
      // don't waste a click. Pin all 5 statuses so the gate doesn't
      // silently drop one.
      const { getByText, queryByRole } = render(
        <PriorityCell job={makeJob({ status })} />,
      );
      expect(getByText("—")).toBeInTheDocument();
      expect(queryByRole("button", { name: "jobs.priority.label" })).toBeNull();
    },
  );

  it("for status='preparing' (not terminal, not editable), renders the badge but no popover trigger", () => {
    // 'preparing' is the in-between state — not editable
    // (canEdit=false) but not in the terminal/running list either.
    // The fallthrough renders the badge alone. Pin so a refactor
    // doesn't accidentally let admins click on a preparing job.
    const { getByText, queryByRole } = render(
      <PriorityCell job={makeJob({ status: "preparing", priority: 0 })} />,
    );
    expect(getByText("jobs.priority.normal")).toBeInTheDocument();
    expect(queryByRole("button", { name: "jobs.priority.label" })).toBeNull();
  });

  it("onChange dispatches patch.mutate with the new priority when next !== current (L64)", async () => {
    // pointerEventsCheck:0 — Radix Popover sets pointer-events:none on body
    // while open; userEvent v14 strict mode rejects subsequent clicks
    // otherwise. Same gotcha as the dropdown-menu tests upstream.
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    render(<PriorityCell job={makeJob({ priority: 0 })} />);
    await user.click(
      screen.getByRole("button", { name: "jobs.priority.label" }),
    );
    // The PriorityToggle inside the popover exposes two buttons whose
    // names are jobs.priority.normal / jobs.priority.high (under the
    // identity t() mock these are the literal i18n keys). Clicking
    // "high" flips priority 0 → 1, so onChange(1) fires patch.mutate.
    const highButton = await screen.findByRole("button", {
      name: "jobs.priority.high",
    });
    await user.click(highButton);
    await waitFor(() => {
      expect(mutateMock).toHaveBeenCalledWith({ id: "job-x", priority: 1 });
    });
  });

  it("onChange short-circuits when next === current (L63 early return)", async () => {
    // Click the badge that matches the current priority — no toggle, no
    // mutation. Guards the `if (next === current) return;` short-circuit
    // against a regression that fires a no-op PATCH every time.
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    render(<PriorityCell job={makeJob({ priority: 0 })} />);
    await user.click(
      screen.getByRole("button", { name: "jobs.priority.label" }),
    );
    // Click the "normal" button — same as current value, so patch.mutate
    // must NOT be called. Use findAllByRole because the trigger badge
    // also exposes 'jobs.priority.normal' in its rendered chip text;
    // pick the popover's PriorityToggle (the role=button with aria-pressed).
    const normalButtons = await screen.findAllByRole("button", {
      name: /jobs\.priority\.normal/,
    });
    // Find the one with aria-pressed (the PriorityToggle button)
    const toggle = normalButtons.find((b) => b.hasAttribute("aria-pressed"));
    expect(toggle).toBeDefined();
    await user.click(toggle!);
    // Brief settle window so any async mutation would have a chance to
    // fire — then assert it didn't.
    await new Promise((r) => setTimeout(r, 50));
    expect(mutateMock).not.toHaveBeenCalled();
  });
});
