import { render } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";

type JobDetail = {
  id: string;
  type: "train" | "evaluate" | "predict";
  status: string;
  mlflow_run_id: string | null;
};

const { queryState } = vi.hoisted(() => ({
  queryState: {
    job: undefined as JobDetail | undefined,
    logText: undefined as string | undefined,
  },
}));

vi.mock("@/api/queries/jobs", () => ({
  useJob: () => ({ data: queryState.job }),
  useJobLogs: () => ({ data: queryState.logText }),
}));

// Each summary / log / artifact child has its own dedicated unit suite.
// Stub them so this test isolates only the route shell's loading
// branch, type-conditional summary selection, and Artifacts-tab gating.
vi.mock("@/components/jobs/JobDetailShell", () => ({
  JobDetailShell: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="stub-job-shell">{children}</div>
  ),
}));
vi.mock("@/components/jobs/TrainSummary", () => ({
  TrainSummary: () => <div data-testid="stub-train-summary" />,
}));
vi.mock("@/components/jobs/EvaluateSummary", () => ({
  EvaluateSummary: () => <div data-testid="stub-evaluate-summary" />,
}));
vi.mock("@/components/jobs/PredictSummary", () => ({
  PredictSummary: () => <div data-testid="stub-predict-summary" />,
}));
vi.mock("@/components/common/LogTail", () => ({
  LogTail: ({ text }: { text: string }) => (
    <pre data-testid="stub-log-tail" data-text={text} />
  ),
}));
vi.mock("@/components/common/ArtifactTree", () => ({
  ArtifactTree: ({ runId }: { runId: string }) => (
    <div data-testid="stub-artifact-tree" data-runid={runId} />
  ),
}));

import JobDetailPage, {
  handle as jobDetailHandle,
} from "@/routes/_authed.jobs.$id";

function renderAt(id = "j-1") {
  return render(
    <MemoryRouter initialEntries={[`/jobs/${id}`]}>
      <Routes>
        <Route path="/jobs/:id" element={<JobDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  queryState.job = undefined;
  queryState.logText = undefined;
});

describe("_authed.jobs.$id.tsx (JobDetailPage)", () => {
  it("renders Loading… while the job query is in flight", () => {
    queryState.job = undefined;
    const { getByText, queryByTestId } = renderAt();
    expect(getByText(/Loading…/)).toBeInTheDocument();
    // The shell + tabs must NOT mount during the loading window.
    expect(queryByTestId("stub-job-shell")).toBeNull();
  });

  it("renders TrainSummary in the Summary tab for type='train'", () => {
    queryState.job = {
      id: "j-1",
      type: "train",
      status: "succeeded",
      mlflow_run_id: "run-1",
    };
    const { getByTestId, queryByTestId } = renderAt();
    expect(getByTestId("stub-train-summary")).toBeInTheDocument();
    expect(queryByTestId("stub-evaluate-summary")).toBeNull();
    expect(queryByTestId("stub-predict-summary")).toBeNull();
  });

  it("renders EvaluateSummary in the Summary tab for type='evaluate'", () => {
    queryState.job = {
      id: "j-1",
      type: "evaluate",
      status: "succeeded",
      mlflow_run_id: "run-1",
    };
    const { getByTestId, queryByTestId } = renderAt();
    expect(getByTestId("stub-evaluate-summary")).toBeInTheDocument();
    expect(queryByTestId("stub-train-summary")).toBeNull();
    expect(queryByTestId("stub-predict-summary")).toBeNull();
  });

  it("renders PredictSummary in the Summary tab for type='predict'", () => {
    queryState.job = {
      id: "j-1",
      type: "predict",
      status: "succeeded",
      mlflow_run_id: "run-1",
    };
    const { getByTestId, queryByTestId } = renderAt();
    expect(getByTestId("stub-predict-summary")).toBeInTheDocument();
    expect(queryByTestId("stub-train-summary")).toBeNull();
    expect(queryByTestId("stub-evaluate-summary")).toBeNull();
  });

  it("passes the log text through to LogTail (truthy case)", async () => {
    queryState.job = {
      id: "j-1",
      type: "train",
      status: "running",
      mlflow_run_id: null,
    };
    queryState.logText = "step=1 loss=0.5";
    const userEvent = (await import("@testing-library/user-event")).default;
    const { getByTestId, getByRole } = renderAt();
    // Radix Tabs unmounts inactive content, so the Summary tab default
    // hides LogTail. Activate the Logs tab to mount it.
    await userEvent.setup().click(getByRole("tab", { name: /Logs/ }));
    expect(getByTestId("stub-log-tail")).toHaveAttribute(
      "data-text",
      "step=1 loss=0.5",
    );
  });

  it("shapes a missing log text to empty string before passing to LogTail", async () => {
    // Defensive `(logText as string) ?? ""` keeps LogTail's text prop
    // a string even before the log query resolves. Pin so a refactor
    // doesn't pass `undefined` and break LogTail.
    queryState.job = {
      id: "j-1",
      type: "train",
      status: "running",
      mlflow_run_id: null,
    };
    queryState.logText = undefined;
    const userEvent = (await import("@testing-library/user-event")).default;
    const { getByTestId, getByRole } = renderAt();
    await userEvent.setup().click(getByRole("tab", { name: /Logs/ }));
    expect(getByTestId("stub-log-tail")).toHaveAttribute("data-text", "");
  });

  it("disables the Artifacts tab when the job has no mlflow_run_id", () => {
    // `disabled={!job.mlflow_run_id}` — pin so jobs that never started
    // (or never reached MLflow) don't show an actionable Artifacts tab
    // that would 404 on click.
    queryState.job = {
      id: "j-1",
      type: "train",
      status: "queued_backend",
      mlflow_run_id: null,
    };
    const { getByRole } = renderAt();
    const artifactsTab = getByRole("tab", { name: /Artifacts/ });
    expect(artifactsTab).toBeDisabled();
  });

  it("enables the Artifacts tab when the job carries an mlflow_run_id", () => {
    queryState.job = {
      id: "j-1",
      type: "train",
      status: "succeeded",
      mlflow_run_id: "run-xyz",
    };
    const { getByRole } = renderAt();
    const artifactsTab = getByRole("tab", { name: /Artifacts/ });
    expect(artifactsTab).not.toBeDisabled();
  });

  it("exports the 'Job' breadcrumb handle", () => {
    expect(jobDetailHandle).toEqual({ breadcrumb: "Job" });
  });
});
