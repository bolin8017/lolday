import { render, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

type RegisteredModel = {
  owner: string;
  name: string;
  description?: string | null;
  latest_version?: number | null;
  latest_production_version?: number | null;
  latest_staging_version?: number | null;
};

const { queryState, lastFilters } = vi.hoisted(() => ({
  queryState: {
    data: undefined as RegisteredModel[] | undefined,
    isLoading: false,
  },
  lastFilters: { current: undefined as Record<string, unknown> | undefined },
}));

vi.mock("@/api/queries/models", () => ({
  useRegisteredModels: (filters: Record<string, unknown>) => {
    lastFilters.current = filters;
    return {
      data: queryState.data,
      isLoading: queryState.isLoading,
    };
  },
}));

// react-i18next: pin t() to identity so the test asserts the i18n key
// rather than translated copy. This avoids cross-locale drift on
// strings that the page renders verbatim (filter options, tooltip
// hints, etc.).
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (k: string) => k,
  }),
}));

// The stub renders each `col.cell` per row into a stable testid so the
// staging / production badge branches and the model-link cell can be
// asserted by the suite. Without invoking the cells the badge JSX is
// structurally unreachable.
vi.mock("@/components/tables/DataTable", () => ({
  DataTable: <TRow,>({
    data,
    columns,
    emptyMessage,
  }: {
    data: TRow[];
    columns: {
      id?: string;
      accessorKey?: string;
      cell?: (ctx: { row: { original: TRow } }) => React.ReactNode;
    }[];
    emptyMessage: string;
  }) => (
    <div data-testid="stub-data-table">
      <span data-testid="stub-row-count">{data.length}</span>
      <span data-testid="stub-empty-message">{emptyMessage}</span>
      {data.map((row, i) => (
        <div key={i} data-testid={`stub-row-${i}`}>
          {columns.map((col, j) => (
            <span
              key={j}
              data-testid={`stub-cell-${i}-${col.id ?? col.accessorKey}`}
            >
              {col.cell ? col.cell({ row: { original: row } }) : null}
            </span>
          ))}
        </div>
      ))}
    </div>
  ),
}));

vi.mock("@/components/layout/PageHeader", () => ({
  PageHeader: ({
    title,
    actions,
  }: {
    title: string;
    actions?: React.ReactNode;
  }) => (
    <header>
      <h1>{title}</h1>
      <div data-testid="stub-header-actions">{actions}</div>
    </header>
  ),
}));

import ModelsListPage, {
  handle as modelsHandle,
} from "@/routes/_authed.models._index";

const DISMISSED_KEY = "lolday.modelsExplainerDismissed";

function renderPage() {
  return render(
    <MemoryRouter>
      <ModelsListPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  queryState.data = undefined;
  queryState.isLoading = false;
  lastFilters.current = undefined;
  // localStorage is JSDOM-backed; reset between tests so the
  // StageExplainerAlert dismiss-persistence assertion is isolated.
  localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("_authed.models._index.tsx (ModelsListPage)", () => {
  it("renders Loading… while the query is in flight", () => {
    queryState.isLoading = true;
    const { getByText, queryByTestId } = renderPage();
    expect(getByText(/Loading…/)).toBeInTheDocument();
    expect(queryByTestId("stub-data-table")).toBeNull();
  });

  it("defaults the visibility filter to 'all' and omits owner on first render", () => {
    queryState.data = [];
    renderPage();
    // Default filter is `visibility=all` + no owner spread; pin so a
    // refactor doesn't silently change the default scope.
    expect(lastFilters.current).toEqual({ visibility: "all" });
  });

  it("passes empty-message + 0 rows through to DataTable when the list is empty", () => {
    queryState.data = [];
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("0");
    expect(getByTestId("stub-empty-message")).toHaveTextContent(
      /No models registered yet\./,
    );
  });

  it("tolerates undefined query.data via the `data ?? []` guard", () => {
    queryState.isLoading = false;
    queryState.data = undefined;
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("0");
  });

  it("renders one row per registered-model item when the query has data", () => {
    queryState.data = [
      {
        owner: "alice",
        name: "elfrf",
        description: "ELF random-forest",
        latest_version: 3,
        latest_production_version: 2,
        latest_staging_version: 3,
      },
      {
        owner: "bob",
        name: "elfcnn",
        description: null,
        latest_version: 1,
        latest_production_version: null,
        latest_staging_version: null,
      },
    ];
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("2");
  });

  it("renders the visibility-filter trigger with a11y label", () => {
    queryState.data = [];
    const { getByLabelText } = renderPage();
    expect(getByLabelText("Filter by visibility")).toBeInTheDocument();
  });

  it("renders the owner-filter Input (placeholder honoured)", () => {
    queryState.data = [];
    const { getByPlaceholderText } = renderPage();
    // i18next is identity-mocked, so the placeholder reflects the key.
    expect(getByPlaceholderText("models.owner")).toBeInTheDocument();
  });

  it("exports the 'Models' breadcrumb handle", () => {
    expect(modelsHandle).toEqual({ breadcrumb: "Models" });
  });

  it("staging cell shows v<N> Badge when latest_staging_version is set; not-promoted otherwise", () => {
    queryState.data = [
      {
        owner: "alice",
        name: "elfrf",
        description: null,
        latest_version: 3,
        latest_production_version: null,
        latest_staging_version: 3,
      },
      {
        owner: "bob",
        name: "elfcnn",
        description: null,
        latest_version: 1,
        latest_production_version: null,
        latest_staging_version: null,
      },
    ];
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-cell-0-staging")).toHaveTextContent("v3");
    expect(getByTestId("stub-cell-1-staging")).toHaveTextContent(
      "models.notPromoted",
    );
  });

  it("production cell shows v<N> Badge when latest_production_version is set; not-promoted otherwise", () => {
    queryState.data = [
      {
        owner: "alice",
        name: "elfrf",
        description: null,
        latest_version: 3,
        latest_production_version: 2,
        latest_staging_version: null,
      },
      {
        owner: "bob",
        name: "elfcnn",
        description: null,
        latest_version: 1,
        latest_production_version: null,
        latest_staging_version: null,
      },
    ];
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-cell-0-prod")).toHaveTextContent("v2");
    expect(getByTestId("stub-cell-1-prod")).toHaveTextContent(
      "models.notPromoted",
    );
  });

  it("model cell renders a router Link with /models/<owner>/<name> target", () => {
    queryState.data = [
      {
        owner: "alice",
        name: "elfrf",
        description: null,
        latest_version: null,
        latest_production_version: null,
        latest_staging_version: null,
      },
    ];
    const { getByTestId } = renderPage();
    const cell = getByTestId("stub-cell-0-model");
    const link = cell.querySelector("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("/models/alice/elfrf");
  });

  it("description cell trims to 80 chars (slice) and falls back to '—' on null", () => {
    const longDescription = "x".repeat(120);
    queryState.data = [
      {
        owner: "alice",
        name: "elfrf",
        description: longDescription,
        latest_version: null,
        latest_production_version: null,
        latest_staging_version: null,
      },
      {
        owner: "bob",
        name: "elfcnn",
        description: null,
        latest_version: null,
        latest_production_version: null,
        latest_staging_version: null,
      },
    ];
    const { getByTestId } = renderPage();
    // slice(0, 80) trims the 120-char string to exactly 80 chars.
    expect(getByTestId("stub-cell-0-description").textContent).toHaveLength(80);
    expect(getByTestId("stub-cell-1-description")).toHaveTextContent("—");
  });

  it("latest_version cell shows v<N> when set; '—' when null", () => {
    queryState.data = [
      {
        owner: "alice",
        name: "elfrf",
        description: null,
        latest_version: 5,
        latest_production_version: null,
        latest_staging_version: null,
      },
      {
        owner: "bob",
        name: "elfcnn",
        description: null,
        latest_version: null,
        latest_production_version: null,
        latest_staging_version: null,
      },
    ];
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-cell-0-latest_version")).toHaveTextContent("v5");
    expect(getByTestId("stub-cell-1-latest_version")).toHaveTextContent("—");
  });

  it("typing in the owner Input forwards owner=<value> into useRegisteredModels filters", async () => {
    queryState.data = [];
    const userEvent = (await import("@testing-library/user-event")).default;
    const { getByPlaceholderText } = renderPage();
    const ownerInput = getByPlaceholderText("models.owner");
    await userEvent.setup().type(ownerInput, "alice");
    // Filters are spread conditionally — `ownerFilter ? { owner } : {}`
    // — so a non-empty string must propagate to the query.
    expect(lastFilters.current).toMatchObject({
      visibility: "all",
      owner: "alice",
    });
  });

  it("choosing a visibility option updates the visibility filter passed to the query", async () => {
    queryState.data = [];
    const userEvent = (await import("@testing-library/user-event")).default;
    const { getByLabelText, getByRole } = renderPage();
    await userEvent.setup().click(getByLabelText("Filter by visibility"));
    await userEvent
      .setup()
      .click(getByRole("option", { name: "models.filter.mine" }));
    // Pins the Select → setVisibility wiring; without this the
    // onValueChange handler is unreachable from the suite.
    expect(lastFilters.current).toMatchObject({ visibility: "mine" });
  });
});

describe("_authed.models._index.tsx (StageExplainerAlert)", () => {
  it("shows the explainer alert on first render", () => {
    queryState.data = [];
    const { getByText } = renderPage();
    // i18next is identity-mocked, so the alert renders the i18n keys
    // verbatim. Pin the title key so a future i18n refactor doesn't
    // silently drop the explainer copy.
    expect(getByText("models.stagesExplainer.title")).toBeInTheDocument();
    expect(getByText("models.stagesExplainer.body")).toBeInTheDocument();
  });

  it("hides the explainer when the dismissed flag is already set in localStorage", () => {
    // Persistent dismissal — re-renders across page navigations must
    // respect a prior dismiss, so the alert is gone on first paint here.
    localStorage.setItem(DISMISSED_KEY, "1");
    queryState.data = [];
    const { queryByText } = renderPage();
    expect(queryByText("models.stagesExplainer.title")).toBeNull();
  });

  it("dismiss button persists the flag to localStorage + hides the alert", async () => {
    queryState.data = [];
    const userEvent = (await import("@testing-library/user-event")).default;
    const { getByLabelText, queryByText } = renderPage();
    const dismiss = getByLabelText("common.dismiss");
    await userEvent.setup().click(dismiss);
    expect(localStorage.getItem(DISMISSED_KEY)).toBe("1");
    expect(queryByText("models.stagesExplainer.title")).toBeNull();
  });
});
