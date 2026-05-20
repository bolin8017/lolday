import { render } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

type User = {
  id: string;
  email: string;
  display_name: string | null;
  role: "user" | "developer" | "admin";
  created_at: string | null;
};

const { authState, queryState, mutateMock } = vi.hoisted(() => ({
  authState: {
    currentUser: { id: "u-self", email: "me@test", role: "admin" } as {
      id: string;
      email: string;
      role: "user" | "developer" | "admin";
    } | null,
  },
  queryState: {
    data: undefined as User[] | undefined,
    isLoading: false,
    isError: false,
    errorStatus: undefined as number | undefined,
    isPending: false,
  },
  mutateMock: vi.fn(),
}));

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({ currentUser: authState.currentUser }),
}));

vi.mock("@/api/queries/admin", () => ({
  useAdminUsers: () => ({
    data: queryState.data,
    isLoading: queryState.isLoading,
    isError: queryState.isError,
    error: queryState.errorStatus
      ? { status: queryState.errorStatus }
      : undefined,
  }),
  useUpdateUserRole: () => ({
    mutate: mutateMock,
    isPending: queryState.isPending,
  }),
}));

// DataTable + PageHeader each carry their own unit suites. Stub them so
// this file exercises only the route shell's branch logic (loading /
// 403-no-permission / default render) and the breadcrumb handle. The
// RoleCell sub-component has its own describe block below.
vi.mock("@/components/tables/DataTable", () => ({
  DataTable: <TRow,>({ data }: { data: TRow[] }) => (
    <div data-testid="stub-data-table">
      <span data-testid="stub-row-count">{data.length}</span>
    </div>
  ),
}));
vi.mock("@/components/layout/PageHeader", () => ({
  PageHeader: ({
    title,
    description,
  }: {
    title: string;
    description?: React.ReactNode;
  }) => (
    <header>
      <h1>{title}</h1>
      {description && (
        <div data-testid="stub-page-header-desc">{description}</div>
      )}
    </header>
  ),
}));

import AdminUsersPage, {
  RoleCell,
  handle as adminUsersHandle,
} from "@/routes/_authed.admin.users";

beforeEach(() => {
  authState.currentUser = {
    id: "u-self",
    email: "me@test",
    role: "admin",
  };
  queryState.data = undefined;
  queryState.isLoading = false;
  queryState.isError = false;
  queryState.errorStatus = undefined;
  queryState.isPending = false;
  mutateMock.mockReset();
});

describe("_authed.admin.users.tsx (AdminUsersPage)", () => {
  it("renders Loading… while the query is in flight", () => {
    queryState.isLoading = true;
    const { getByText, queryByTestId } = render(<AdminUsersPage />);
    expect(getByText(/Loading…/)).toBeInTheDocument();
    expect(queryByTestId("stub-data-table")).toBeNull();
  });

  it("renders the operator-ask copy when the API returns 403", () => {
    // Non-admin users hit /admin/users and the backend returns 403; the
    // page short-circuits with a single PageHeader instead of the table.
    // Pin the operator-facing copy so a refactor doesn't silently swallow
    // the upgrade-path instruction.
    queryState.isError = true;
    queryState.errorStatus = 403;
    const { getByText, queryByTestId } = render(<AdminUsersPage />);
    expect(getByText(/does not have admin permission/i)).toBeInTheDocument();
    expect(getByText(/Ask the lolday operator/i)).toBeInTheDocument();
    // The 403 branch must NOT render the table.
    expect(queryByTestId("stub-data-table")).toBeNull();
  });

  it("falls through to an empty table on non-403 errors (data ?? [])", () => {
    // A 500 / 503 doesn't take the 403 branch; the `data ?? []` guard
    // shapes undefined into an empty list and renders the normal layout.
    queryState.isError = true;
    queryState.errorStatus = 500;
    queryState.data = undefined;
    const { getByTestId } = render(<AdminUsersPage />);
    expect(getByTestId("stub-row-count")).toHaveTextContent("0");
  });

  it("renders the page header description with the role-promotion copy", () => {
    queryState.data = [];
    const { getByTestId } = render(<AdminUsersPage />);
    const desc = getByTestId("stub-page-header-desc");
    expect(desc.textContent).toMatch(/developer/);
    expect(desc.textContent).toMatch(/admin/);
    expect(desc.textContent).toMatch(/user/);
  });

  it("passes the user list through to DataTable when the query has data", () => {
    queryState.data = [
      {
        id: "u-self",
        email: "me@test",
        display_name: "Me",
        role: "admin",
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "u-2",
        email: "other@test",
        display_name: null,
        role: "user",
        created_at: null,
      },
    ];
    const { getByTestId } = render(<AdminUsersPage />);
    expect(getByTestId("stub-row-count")).toHaveTextContent("2");
  });

  it("exports the 'Admin / Users' breadcrumb handle", () => {
    expect(adminUsersHandle).toEqual({ breadcrumb: "Admin / Users" });
  });
});

describe("_authed.admin.users.tsx (RoleCell)", () => {
  function makeUser(overrides: Partial<User> = {}): User {
    return {
      id: "u-other",
      email: "other@test",
      display_name: null,
      role: "user",
      created_at: "2026-01-01T00:00:00Z",
      ...overrides,
    };
  }

  it("renders a Select trigger with aria-label='Role for <email>'", () => {
    const { getByLabelText } = render(
      <RoleCell user={makeUser()} selfId="u-self" />,
    );
    // a11y rule (axe button-name / select-name): every Role select must
    // carry the user email so screen readers can disambiguate the
    // row-level selects on the admin users table.
    expect(getByLabelText("Role for other@test")).toBeInTheDocument();
  });

  it("disables the trigger while the mutation is pending", () => {
    queryState.isPending = true;
    const { getByLabelText } = render(
      <RoleCell user={makeUser()} selfId="u-self" />,
    );
    // Radix Select trigger reflects `disabled` via `data-disabled` +
    // the underlying button's `disabled` attribute.
    expect(getByLabelText("Role for other@test")).toBeDisabled();
  });

  it("for a non-self row, all three roles are selectable", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const { getByLabelText, getByRole } = render(
      <RoleCell user={makeUser()} selfId="u-self" />,
    );
    await userEvent.setup().click(getByLabelText("Role for other@test"));
    // Radix Select renders the listbox lazily on open.
    // None of the three options should carry data-disabled.
    expect(getByRole("option", { name: "user" })).not.toHaveAttribute(
      "data-disabled",
    );
    expect(getByRole("option", { name: "developer" })).not.toHaveAttribute(
      "data-disabled",
    );
    expect(getByRole("option", { name: "admin" })).not.toHaveAttribute(
      "data-disabled",
    );
  });

  it("for the self row, only 'admin' is selectable (cannot self-demote)", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const self = makeUser({ id: "u-self", email: "me@test", role: "admin" });
    const { getByLabelText, getByRole } = render(
      <RoleCell user={self} selfId="u-self" />,
    );
    await userEvent.setup().click(getByLabelText("Role for me@test"));
    // user + developer disabled; admin (current value) enabled. Without
    // this safeguard an admin could lock the platform out of admin access
    // by dropping themselves to user / developer.
    expect(getByRole("option", { name: "user" })).toHaveAttribute(
      "data-disabled",
    );
    expect(getByRole("option", { name: "developer" })).toHaveAttribute(
      "data-disabled",
    );
    expect(getByRole("option", { name: "admin" })).not.toHaveAttribute(
      "data-disabled",
    );
  });
});
