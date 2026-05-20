import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import ExperimentsListPage from "@/routes/_authed.runs._index";

type ExpRow = {
  experiment_id: string;
  name: string;
  run_count: number | null;
  best_f1: number | null;
  latest_start_time: number | null;
};

const { queryState } = vi.hoisted(() => ({
  queryState: {
    data: undefined as ExpRow[] | undefined,
    isLoading: false,
  },
}));

vi.mock("@/api/queries/runs", () => ({
  useExperimentsWithStats: () => ({
    data: queryState.data,
    isLoading: queryState.isLoading,
  }),
}));

// ExperimentCard pulls in OpenInMlflowButton + date helpers + a Link;
// stub it to isolate the route shell's render-the-list-or-skip logic.
vi.mock("@/components/runs/ExperimentCard", () => ({
  ExperimentCard: ({ exp }: { exp: ExpRow }) => (
    <article data-testid="exp-card">{exp.name}</article>
  ),
}));
vi.mock("@/components/layout/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

beforeEach(() => {
  queryState.data = undefined;
  queryState.isLoading = false;
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ExperimentsListPage />
    </MemoryRouter>,
  );
}

describe("_authed.runs._index.tsx (ExperimentsListPage)", () => {
  it("renders Loading… while the query is fetching", () => {
    queryState.isLoading = true;
    const { getByText, queryByTestId } = renderPage();
    expect(getByText(/Loading…/)).toBeInTheDocument();
    expect(queryByTestId("exp-card")).toBeNull();
  });

  it("renders the header but no cards when the experiment list is empty", () => {
    queryState.isLoading = false;
    queryState.data = [];
    const { getByText, queryAllByTestId } = renderPage();
    expect(getByText(/Experiments/)).toBeInTheDocument();
    expect(queryAllByTestId("exp-card")).toHaveLength(0);
  });

  it("tolerates undefined data without throwing (defensive `data ?? []`)", () => {
    queryState.isLoading = false;
    queryState.data = undefined;
    const { getByText, queryAllByTestId } = renderPage();
    // Header still renders; the `(data ?? [])` guard keeps the map from
    // throwing when the query resolves with undefined (transient between
    // loading and first fetch in some TanStack-Query edge paths).
    expect(getByText(/Experiments/)).toBeInTheDocument();
    expect(queryAllByTestId("exp-card")).toHaveLength(0);
  });

  it("renders one card per experiment row when the query has data", () => {
    queryState.isLoading = false;
    queryState.data = [
      {
        experiment_id: "exp-1",
        name: "First Experiment",
        run_count: 3,
        best_f1: 0.92,
        latest_start_time: 1700000000,
      },
      {
        experiment_id: "exp-2",
        name: "Second Experiment",
        run_count: null,
        best_f1: null,
        latest_start_time: null,
      },
    ];
    const { getByText, queryAllByTestId } = renderPage();
    expect(getByText(/Experiments/)).toBeInTheDocument();
    const cards = queryAllByTestId("exp-card");
    expect(cards).toHaveLength(2);
    expect(getByText(/First Experiment/)).toBeInTheDocument();
    expect(getByText(/Second Experiment/)).toBeInTheDocument();
  });
});
