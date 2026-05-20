import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { ColumnDef } from "@tanstack/react-table";

type Detector = {
  id: string;
  name: string;
  display_name: string;
  git_url: string;
  description: string | null;
  created_at: string;
};

type VersionRow = {
  id: string;
  git_tag: string;
  git_sha: string;
  status: string;
  built_at: string;
};

type BuildRow = {
  id: string;
  git_tag: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  log_tail: string | null;
};

type AvailableTag = { name: string; commit_sha: string };

type VersionDetail = {
  manifest: Record<string, unknown> | null;
} | null;

const {
  queryState,
  capturedManifestProp,
  triggerBuildMock,
  cancelBuildMock,
  deleteDetectorMock,
  deleteVersionMock,
} = vi.hoisted(() => ({
  queryState: {
    detector: undefined as Detector | undefined,
    versions: undefined as VersionRow[] | { items: VersionRow[] } | undefined,
    builds: undefined as BuildRow[] | { items: BuildRow[] } | undefined,
    tags: undefined as AvailableTag[] | undefined,
    versionDetail: undefined as VersionDetail | undefined,
    versionDetailLoading: false,
    versionDetailError: null as Error | null,
  },
  capturedManifestProp: { current: undefined as unknown },
  triggerBuildMock: vi.fn(),
  cancelBuildMock: vi.fn(),
  deleteDetectorMock: vi.fn(),
  deleteVersionMock: vi.fn(),
}));

vi.mock("@/api/queries/detectors", () => ({
  useDetector: () => ({ data: queryState.detector }),
  useDetectorVersions: () => ({ data: queryState.versions }),
  useDetectorBuilds: () => ({ data: queryState.builds }),
  useAvailableTags: () => ({ data: queryState.tags }),
  useTriggerBuild: () => ({
    mutate: triggerBuildMock,
    mutateAsync: triggerBuildMock,
    isPending: false,
  }),
  useCancelBuild: () => ({
    mutate: cancelBuildMock,
    isPending: false,
  }),
  useDetectorVersion: () => ({
    data: queryState.versionDetail,
    isLoading: queryState.versionDetailLoading,
    error: queryState.versionDetailError,
  }),
  useDeleteDetector: () => ({
    mutate: deleteDetectorMock,
    mutateAsync: deleteDetectorMock,
    isPending: false,
  }),
  useDeleteVersion: () => ({
    mutate: deleteVersionMock,
    mutateAsync: deleteVersionMock,
    isPending: false,
  }),
}));

// Heavy children have their own dedicated suites. Stub so this test
// isolates the route shell — tab switching, payload unwrapping,
// ManifestView branches, and the trigger-build dialog gating.
vi.mock("@/components/common/JsonTreeView", () => ({
  JsonTreeView: ({ value }: { value: unknown }) => {
    capturedManifestProp.current = value;
    return <div data-testid="stub-json-tree" />;
  },
}));
vi.mock("@/components/common/LogTail", () => ({
  LogTail: ({ text }: { text: string }) => (
    <pre data-testid="stub-log-tail" data-text={text} />
  ),
}));
vi.mock("@/components/common/StatusBadge", () => ({
  StatusBadge: ({ status }: { status: string }) => (
    <span data-testid="stub-status-badge">{status}</span>
  ),
}));
vi.mock("@/components/common/DeleteConfirmDialog", () => ({
  DeleteConfirmDialog: ({ open, title }: { open: boolean; title: string }) =>
    open ? <div data-testid="stub-delete-dialog">{title}</div> : null,
}));
vi.mock("@/components/tables/DataTable", () => ({
  DataTable: <TRow,>({
    data,
    columns,
    emptyMessage,
  }: {
    data: TRow[];
    columns: ColumnDef<TRow>[];
    emptyMessage: string;
  }) => (
    <div data-testid="stub-data-table">
      <span data-testid="stub-row-count">{data.length}</span>
      <span data-testid="stub-col-count">{columns.length}</span>
      <span data-testid="stub-empty-msg">{emptyMessage}</span>
    </div>
  ),
}));

import DetectorDetailPage, {
  handle as detectorDetailHandle,
} from "@/routes/_authed.detectors.$id";

function renderAt(id = "det-1") {
  return render(
    <MemoryRouter initialEntries={[`/detectors/${id}`]}>
      <Routes>
        <Route path="/detectors/:id" element={<DetectorDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  queryState.detector = undefined;
  queryState.versions = undefined;
  queryState.builds = undefined;
  queryState.tags = undefined;
  queryState.versionDetail = undefined;
  queryState.versionDetailLoading = false;
  queryState.versionDetailError = null;
  capturedManifestProp.current = undefined;
  triggerBuildMock.mockReset();
  cancelBuildMock.mockReset();
  deleteDetectorMock.mockReset();
  deleteVersionMock.mockReset();
});

const baseDetector: Detector = {
  id: "det-1",
  name: "elf-rf",
  display_name: "ELF Random Forest",
  git_url: "git@github.com:bolin8017/elfrfdet.git",
  description: "Random-forest ELF classifier",
  created_at: "2026-04-01T00:00:00Z",
};

describe("_authed.detectors.$id.tsx (DetectorDetailPage)", () => {
  it("renders Loading… while the detector query is in flight", () => {
    queryState.detector = undefined;
    renderAt();
    expect(screen.getByText(/Loading…/)).toBeInTheDocument();
    // Tabs must not mount during the loading window.
    expect(screen.queryByRole("tab", { name: /Overview/ })).toBeNull();
  });

  it("renders the metadata card with name / git_url / description in the Overview tab", () => {
    queryState.detector = baseDetector;
    renderAt();
    expect(screen.getByText("elf-rf")).toBeInTheDocument();
    expect(
      screen.getByText("git@github.com:bolin8017/elfrfdet.git"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Random-forest ELF classifier"),
    ).toBeInTheDocument();
  });

  it("renders the em-dash fallback when description is null", () => {
    queryState.detector = { ...baseDetector, description: null };
    renderAt();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("unwraps versions payload from an envelope `{ items: [...] }`", async () => {
    const user = userEvent.setup();
    queryState.detector = baseDetector;
    queryState.versions = {
      items: [
        {
          id: "v-1",
          git_tag: "v1.0.0",
          git_sha: "abcdef1234567890",
          status: "ready",
          built_at: "2026-05-01T00:00:00Z",
        },
        {
          id: "v-2",
          git_tag: "v1.1.0",
          git_sha: "1234567890abcdef",
          status: "ready",
          built_at: "2026-05-10T00:00:00Z",
        },
      ],
    };
    renderAt();
    await user.click(screen.getByRole("tab", { name: /Versions/ }));
    // The Versions DataTable should now read two rows.
    expect(screen.getAllByTestId("stub-row-count")[0]).toHaveTextContent("2");
  });

  it("unwraps versions payload from a bare array", async () => {
    const user = userEvent.setup();
    queryState.detector = baseDetector;
    queryState.versions = [
      {
        id: "v-1",
        git_tag: "v1.0.0",
        git_sha: "abcdef1234567890",
        status: "ready",
        built_at: "2026-05-01T00:00:00Z",
      },
    ];
    renderAt();
    await user.click(screen.getByRole("tab", { name: /Versions/ }));
    expect(screen.getAllByTestId("stub-row-count")[0]).toHaveTextContent("1");
  });

  it("falls back to [] when versions payload is malformed", async () => {
    const user = userEvent.setup();
    queryState.detector = baseDetector;
    // Object that's neither an array nor `{ items: [...] }`.
    queryState.versions = { other: "shape" } as never;
    renderAt();
    await user.click(screen.getByRole("tab", { name: /Versions/ }));
    expect(screen.getAllByTestId("stub-row-count")[0]).toHaveTextContent("0");
  });

  it("renders the Builds tab with the Trigger build button", async () => {
    const user = userEvent.setup();
    queryState.detector = baseDetector;
    queryState.builds = [
      {
        id: "b-1",
        git_tag: "v1.0.0",
        status: "succeeded",
        started_at: "2026-05-01T00:00:00Z",
        finished_at: "2026-05-01T00:05:00Z",
        log_tail: null,
      },
    ];
    renderAt();
    await user.click(screen.getByRole("tab", { name: /Builds/ }));
    expect(
      screen.getByRole("button", { name: /Trigger build/ }),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("stub-row-count")[0]).toHaveTextContent("1");
  });

  it("opens the DeleteConfirmDialog when the Delete button in the header is clicked", async () => {
    const user = userEvent.setup();
    queryState.detector = baseDetector;
    renderAt();
    expect(screen.queryByTestId("stub-delete-dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: /^Delete$/ }));
    expect(screen.getByText(/Delete detector elf-rf\?/)).toBeInTheDocument();
  });

  it("exports the 'Detector' breadcrumb handle", () => {
    expect(detectorDetailHandle).toEqual({ breadcrumb: "Detector" });
  });
});
