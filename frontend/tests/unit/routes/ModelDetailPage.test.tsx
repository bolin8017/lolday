import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";

type ModelDetail = {
  owner_handle: string;
  name: string;
  description: string | null;
  tags: Record<string, string> | null;
  created_at: string;
};

type ModelVersion = {
  id: string;
  mlflow_version: number;
  current_stage: "None" | "Staging" | "Production" | "Archived";
  visibility: "public" | "private";
  mlflow_run_id: string;
  created_at: string;
};

type CurrentUser = {
  handle: string;
  role: "user" | "developer" | "admin";
} | null;

const { detailState, versionsState, meState, capturedTransitionProps } =
  vi.hoisted(() => ({
    detailState: {
      data: undefined as ModelDetail | undefined,
      isLoading: false,
      isError: false,
    },
    versionsState: {
      data: undefined as ModelVersion[] | undefined,
      isLoading: false,
    },
    meState: {
      data: null as CurrentUser,
      isLoading: false,
    },
    capturedTransitionProps: {
      current: undefined as { hasExistingProd?: boolean } | undefined,
    },
  }));

vi.mock("@/api/queries/models", () => ({
  useModelDetail: () => detailState,
  useModelVersions: () => versionsState,
  useUpdateModelDescription: () => ({ mutateAsync: vi.fn() }),
  useUpdateModelTags: () => ({ mutateAsync: vi.fn() }),
  useTransferOwner: () => ({ mutateAsync: vi.fn() }),
  useDeleteModel: () => ({ mutateAsync: vi.fn() }),
  useDeleteVersion: () => ({ mutateAsync: vi.fn() }),
  useUpdateVisibility: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock("@/api/queries/auth", () => ({
  useCurrentUser: () => meState,
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

vi.mock("@/hooks/use-toast", () => ({ toast: vi.fn() }));

// Heavy children — keep the shell test fast and focused on branching.
vi.mock("@/components/common/MarkdownView", () => ({
  MarkdownView: ({ source }: { source: string }) => (
    <div data-testid="stub-markdown" data-source={source} />
  ),
}));
vi.mock("@/components/users/OwnerLabel", () => ({
  OwnerLabel: ({ handle }: { handle: string }) => (
    <span data-testid="stub-owner-label">{handle}</span>
  ),
}));
vi.mock("@/components/models/VisibilityBadge", () => ({
  VisibilityBadge: ({ visibility }: { visibility: string }) => (
    <span data-testid="stub-visibility-badge">{visibility}</span>
  ),
}));
vi.mock("@/components/forms/ModelDescriptionEditor", () => ({
  ModelDescriptionEditor: ({ open }: { open: boolean }) =>
    open ? <div data-testid="stub-desc-editor" /> : null,
}));
vi.mock("@/components/forms/ModelTagsEditor", () => ({
  ModelTagsEditor: ({ open }: { open: boolean }) =>
    open ? <div data-testid="stub-tags-editor" /> : null,
}));
vi.mock("@/components/forms/OwnerTransferDialog", () => ({
  OwnerTransferDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="stub-transfer" /> : null,
}));
vi.mock("@/components/forms/DeleteModelDialog", () => ({
  DeleteModelDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="stub-delete-model" /> : null,
}));
vi.mock("@/components/forms/ModelVisibilityDialog", () => ({
  ModelVisibilityDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="stub-visibility-dialog" /> : null,
}));
vi.mock("@/components/forms/ModelTransitionDialog", () => ({
  ModelTransitionDialog: (props: {
    open: boolean;
    hasExistingProd: boolean;
  }) => {
    capturedTransitionProps.current = props;
    return props.open ? <div data-testid="stub-transition" /> : null;
  },
}));

import ModelDetailPage, {
  handle as modelDetailHandle,
} from "@/routes/_authed.models.$owner.$name";

function renderAt(owner = "alice", name = "elf-rf") {
  return render(
    <MemoryRouter initialEntries={[`/models/${owner}/${name}`]}>
      <Routes>
        <Route path="/models/:owner/:name" element={<ModelDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  detailState.data = undefined;
  detailState.isLoading = false;
  detailState.isError = false;
  versionsState.data = undefined;
  versionsState.isLoading = false;
  meState.data = null;
  meState.isLoading = false;
  capturedTransitionProps.current = undefined;
});

const baseModel: ModelDetail = {
  owner_handle: "alice",
  name: "elf-rf",
  description: null,
  tags: null,
  created_at: "2026-04-01T00:00:00Z",
};

describe("_authed.models.$owner.$name.tsx (ModelDetailPage)", () => {
  it("renders Loading… while the detail query is in flight", () => {
    detailState.isLoading = true;
    renderAt();
    expect(screen.getByText(/Loading…/)).toBeInTheDocument();
  });

  it("renders Loading… while the current-user query is in flight", () => {
    detailState.data = baseModel;
    meState.isLoading = true;
    renderAt();
    expect(screen.getByText(/Loading…/)).toBeInTheDocument();
  });

  it("renders 'Model not found.' when the detail query errors", () => {
    detailState.isError = true;
    renderAt();
    expect(screen.getByText(/Model not found\./)).toBeInTheDocument();
  });

  it("renders 'Model not found.' when the detail query resolves to no data", () => {
    detailState.data = undefined;
    renderAt();
    expect(screen.getByText(/Model not found\./)).toBeInTheDocument();
  });

  it("renders the description-empty fallback when description is null", () => {
    detailState.data = baseModel;
    renderAt();
    expect(screen.getByText("models.description.empty")).toBeInTheDocument();
    expect(screen.queryByTestId("stub-markdown")).toBeNull();
  });

  it("renders MarkdownView when description is present", () => {
    detailState.data = { ...baseModel, description: "## docs" };
    renderAt();
    const md = screen.getByTestId("stub-markdown");
    expect(md.dataset.source).toBe("## docs");
  });

  it("renders the tags-empty fallback when tags is null", () => {
    detailState.data = baseModel;
    renderAt();
    expect(screen.getByText("models.tags.empty")).toBeInTheDocument();
  });

  it("renders tag chips when tags has entries", () => {
    detailState.data = { ...baseModel, tags: { framework: "torch", v: "2" } };
    renderAt();
    expect(screen.getByText(/framework=torch/)).toBeInTheDocument();
    expect(screen.getByText(/v=2/)).toBeInTheDocument();
  });

  it("hides the header dropdown when the current user is neither owner nor admin", () => {
    detailState.data = baseModel;
    meState.data = { handle: "bob", role: "user" };
    renderAt();
    expect(
      screen.queryByRole("button", { name: "more" }),
    ).not.toBeInTheDocument();
  });

  it("shows the header dropdown when the current user is the owner", () => {
    detailState.data = baseModel;
    meState.data = { handle: "alice", role: "user" };
    renderAt();
    expect(
      screen.getAllByRole("button", { name: "more" }).length,
    ).toBeGreaterThan(0);
  });

  it("shows the header dropdown when the current user is an admin", () => {
    detailState.data = baseModel;
    meState.data = { handle: "carol", role: "admin" };
    renderAt();
    expect(
      screen.getAllByRole("button", { name: "more" }).length,
    ).toBeGreaterThan(0);
  });

  it("renders Loading… in the Versions section while the versions query is in flight", () => {
    detailState.data = baseModel;
    versionsState.isLoading = true;
    renderAt();
    // The page-level Loading… branch returns early, so the Versions-section
    // Loading… renders only when the detail query has resolved.
    expect(screen.getAllByText(/Loading…/).length).toBeGreaterThanOrEqual(1);
  });

  it("renders 'No versions yet.' when the versions list is empty", () => {
    detailState.data = baseModel;
    versionsState.data = [];
    renderAt();
    expect(screen.getByText(/No versions yet\./)).toBeInTheDocument();
  });

  it("renders one table row per model version", () => {
    detailState.data = baseModel;
    versionsState.data = [
      {
        id: "mv-1",
        mlflow_version: 1,
        current_stage: "None",
        visibility: "public",
        mlflow_run_id: "abcdef1234567890",
        created_at: "2026-05-01T00:00:00Z",
      },
      {
        id: "mv-2",
        mlflow_version: 2,
        current_stage: "Production",
        visibility: "private",
        mlflow_run_id: "1234567890abcdef",
        created_at: "2026-05-10T00:00:00Z",
      },
    ];
    renderAt();
    const table = screen.getByRole("table");
    expect(within(table).getByText("v1")).toBeInTheDocument();
    expect(within(table).getByText("v2")).toBeInTheDocument();
    // Visibility badge stubs both render — one per row.
    expect(within(table).getAllByTestId("stub-visibility-badge")).toHaveLength(
      2,
    );
  });

  it("exports the 'Model' breadcrumb handle", () => {
    expect(modelDetailHandle).toEqual({ breadcrumb: "Model" });
  });
});
