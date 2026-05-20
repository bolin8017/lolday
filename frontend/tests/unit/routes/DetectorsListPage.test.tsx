import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";

type Detector = {
  id: string;
  name: string;
  display_name: string;
  description: string | null;
  git_url: string;
  created_at: string;
};

const { queryState, navigateMock } = vi.hoisted(() => ({
  queryState: {
    data: undefined as { items: Detector[] } | undefined,
    isLoading: false,
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

vi.mock("@/api/queries/detectors", () => ({
  useDetectors: () => ({
    data: queryState.data,
    isLoading: queryState.isLoading,
  }),
  // The route's DetectorRowActions sub-component calls useDeleteDetector
  // even when no rows render; stub it so the import doesn't pull in the
  // real TanStack mutation factory (which would need a QueryClient).
  useDeleteDetector: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

// DataTable + PageHeader each have dedicated unit suites. Stub them so
// this test exercises only the route shell's loading / shaping / CTA /
// breadcrumb plumbing. The row-action delete dialog flow is a separate
// concern best covered by a dedicated DetectorRowActions test.
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

import DetectorsListPage, {
  handle as detectorsHandle,
} from "@/routes/_authed.detectors._index";

function renderPage() {
  return render(
    <MemoryRouter>
      <DetectorsListPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  queryState.data = undefined;
  queryState.isLoading = false;
  navigateMock.mockReset();
});

describe("_authed.detectors._index.tsx (DetectorsListPage)", () => {
  it("renders Loading… while the query is in flight", () => {
    queryState.isLoading = true;
    const { getByText, queryByTestId } = renderPage();
    expect(getByText(/Loading…/)).toBeInTheDocument();
    expect(queryByTestId("stub-data-table")).toBeNull();
  });

  it("renders the Register CTA link pointing at /detectors/new", () => {
    queryState.data = { items: [] };
    const { getByRole } = renderPage();
    const register = getByRole("link", { name: /Register/i });
    expect(register).toHaveAttribute("href", "/detectors/new");
  });

  it("passes the empty-message string through to DataTable when the list is empty", () => {
    queryState.data = { items: [] };
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("0");
    expect(getByTestId("stub-empty-message")).toHaveTextContent(
      /No detectors registered yet\./,
    );
  });

  it("tolerates an undefined query.data shape via the `?.items ?? []` guard", () => {
    // Same defensive guard the datasets list page carries — pin the
    // transient undefined-between-loading-and-first-fetch case so a
    // refactor doesn't crash the render.
    queryState.isLoading = false;
    queryState.data = undefined;
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("0");
  });

  it("renders one row per detector item when the query has data", () => {
    queryState.data = {
      items: [
        {
          id: "det-1",
          name: "elfrf",
          display_name: "ELF RF",
          description: "ELF random-forest classifier",
          git_url: "https://github.com/bolin8017/elfrfdet",
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "det-2",
          name: "elfcnn",
          display_name: "ELF CNN",
          description: null,
          git_url: "https://github.com/bolin8017/elfcnndet",
          created_at: "2026-02-01T00:00:00Z",
        },
      ],
    };
    const { getByTestId } = renderPage();
    expect(getByTestId("stub-row-count")).toHaveTextContent("2");
  });

  it("navigates to /detectors/:id when a row is clicked", async () => {
    queryState.data = {
      items: [
        {
          id: "det-42",
          name: "elfrf",
          display_name: "ELF RF",
          description: null,
          git_url: "https://github.com/bolin8017/elfrfdet",
          created_at: "2026-03-01T00:00:00Z",
        },
      ],
    };
    const userEvent = (await import("@testing-library/user-event")).default;
    const { getByTestId } = renderPage();
    await userEvent.setup().click(getByTestId("stub-row-0"));
    expect(navigateMock).toHaveBeenCalledWith("/detectors/det-42");
  });

  it("exports the 'Detectors' breadcrumb handle", () => {
    expect(detectorsHandle).toEqual({ breadcrumb: "Detectors" });
  });
});
