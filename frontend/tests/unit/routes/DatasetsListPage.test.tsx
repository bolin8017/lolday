import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";

type Dataset = {
  id: string;
  name: string;
  visibility: "public" | "private";
  sample_count: number;
  created_at: string;
};

const { queryState, navigateMock } = vi.hoisted(() => ({
  queryState: {
    data: undefined as { items: Dataset[] } | undefined,
    isLoading: false,
    lastVisibility: undefined as "public" | "private" | "all" | undefined,
  },
  navigateMock: vi.fn(),
}));

vi.mock("react-router", async () => {
  const actual =
    await vi.importActual<typeof import("react-router")>("react-router");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock("@/api/queries/datasets", () => ({
  useDatasets: (visibility: "public" | "private" | "all") => {
    queryState.lastVisibility = visibility;
    return {
      data: queryState.data,
      isLoading: queryState.isLoading,
    };
  },
}));

// DataTable + PageHeader each pull their own deep tree (column rendering,
// row-click handler, action slot). Stub them so this file exercises only
// the list-page's loading / filter / data-shaping / row-click-wiring
// logic. Both have their own unit suites.
vi.mock("@/components/tables/DataTable", () => ({
  DataTable: <TRow,>({
    data,
    emptyMessage,
    onRowClick,
  }: {
    data: TRow[];
    emptyMessage: string;
    onRowClick?: (row: TRow) => void;
  }) => (
    <div data-testid="stub-data-table">
      <span data-testid="stub-empty-message">{emptyMessage}</span>
      <span data-testid="stub-row-count">{data.length}</span>
      {data.map((row, i) => (
        <button
          key={i}
          type="button"
          data-testid={`stub-row-${i}`}
          onClick={() => onRowClick?.(row)}
        >
          row-{i}
        </button>
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

import DatasetsListPage, {
  handle as datasetsHandle,
} from "@/routes/_authed.datasets._index";

function renderPage() {
  return render(
    <MemoryRouter>
      <DatasetsListPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  queryState.data = undefined;
  queryState.isLoading = false;
  queryState.lastVisibility = undefined;
  navigateMock.mockReset();
});

describe("_authed.datasets._index.tsx (DatasetsListPage)", () => {
  it("renders Loading… while the query is in flight", () => {
    queryState.isLoading = true;
    const { getByText, queryByTestId } = renderPage();
    expect(getByText(/Loading…/)).toBeInTheDocument();
    expect(queryByTestId("stub-data-table")).toBeNull();
  });

  it("defaults the visibility filter to 'all' on first render", () => {
    queryState.data = { items: [] };
    renderPage();
    // The route uses useState<...>('all'), so the first useDatasets call
    // must receive 'all'. Pin so a future refactor doesn't silently
    // change the default scope (private-by-default would hide public
    // datasets from users without datasets of their own).
    expect(queryState.lastVisibility).toBe("all");
  });

  it("renders the Upload CTA link pointing at /datasets/new", () => {
    queryState.data = { items: [] };
    const { getByRole } = renderPage();
    const upload = getByRole("link", { name: /Upload/i });
    expect(upload).toHaveAttribute("href", "/datasets/new");
  });

  it("renders the visibility filter trigger inside the header actions", () => {
    queryState.data = { items: [] };
    const { getByLabelText } = renderPage();
    // The Radix Select trigger carries aria-label="Filter by visibility";
    // pin the label so a a11y regression on this filter is loud.
    expect(getByLabelText("Filter by visibility")).toBeInTheDocument();
  });

  it("passes the empty-message string through to DataTable when the list is empty", () => {
    queryState.data = { items: [] };
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("0");
    expect(getByTestId("stub-empty-message")).toHaveTextContent(
      /No datasets yet\./,
    );
  });

  it("tolerates an undefined query.data shape via the `?.items ?? []` guard", () => {
    // The route shapes the response as `(data as {items?: Dataset[]} | undefined)?.items ?? []`.
    // Pin the guard so a transient undefined between loading and first
    // fetch doesn't crash the render.
    queryState.isLoading = false;
    queryState.data = undefined;
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("0");
  });

  it("renders one row per dataset item when the query has data", () => {
    queryState.data = {
      items: [
        {
          id: "ds-1",
          name: "First",
          visibility: "public",
          sample_count: 100,
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "ds-2",
          name: "Second",
          visibility: "private",
          sample_count: 50,
          created_at: "2026-02-01T00:00:00Z",
        },
      ],
    };
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("2");
  });

  it("navigates to /datasets/:id when a row is clicked", async () => {
    queryState.data = {
      items: [
        {
          id: "ds-42",
          name: "Clicked",
          visibility: "public",
          sample_count: 1,
          created_at: "2026-03-01T00:00:00Z",
        },
      ],
    };
    const userEvent = (await import("@testing-library/user-event")).default;
    const { getByTestId } = renderPage();
    await userEvent.setup().click(getByTestId("stub-row-0"));
    expect(navigateMock).toHaveBeenCalledWith("/datasets/ds-42");
  });

  it("exports the 'Datasets' breadcrumb handle", () => {
    expect(datasetsHandle).toEqual({ breadcrumb: "Datasets" });
  });
});
