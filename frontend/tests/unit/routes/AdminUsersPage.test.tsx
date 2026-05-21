import { render } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

type User = {
  id: string;
  email: string;
  handle: string;
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
// The stub invokes each cell renderer so column-cell branches in the
// route file are exercised by render assertions, not stubbed out entirely.
// Otherwise the email / role / created_at cell logic (badge for self,
// `display_name ?? "—"`, formatRelative) is structurally unreachable
// from the suite — pinning those cells protects against an accidental
// regression that breaks the admin table.
vi.mock("@/components/tables/DataTable", () => ({
  DataTable: <TRow,>({
    data,
    columns,
  }: {
    data: TRow[];
    columns: {
      id?: string;
      accessorKey?: string;
      cell?: (ctx: { row: { original: TRow } }) => React.ReactNode;
    }[];
  }) => (
    <div data-testid="stub-data-table">
      <span data-testid="stub-row-count">{data.length}</span>
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
        handle: "me",
        display_name: "Me",
        role: "admin",
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "u-2",
        email: "other@test",
        handle: "other",
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

  it("renders the email cell with the 'you' badge for the current user only", () => {
    queryState.data = [
      {
        id: "u-self",
        email: "me@test",
        handle: "me",
        display_name: "Me",
        role: "admin",
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "u-2",
        email: "other@test",
        handle: "other",
        display_name: null,
        role: "user",
        created_at: null,
      },
    ];
    const { getByTestId } = render(<AdminUsersPage />);
    const selfEmail = getByTestId("stub-cell-0-email");
    const otherEmail = getByTestId("stub-cell-1-email");
    // Self row gets the disambiguating "you" badge.
    expect(selfEmail.textContent).toContain("me@test");
    expect(selfEmail.textContent).toContain("you");
    expect(otherEmail.textContent).toContain("other@test");
    expect(otherEmail.textContent).not.toContain("you");
  });

  it("display_name cell falls back to '—' when null", () => {
    queryState.data = [
      {
        id: "u-1",
        email: "a@test",
        handle: "a",
        display_name: null,
        role: "user",
        created_at: null,
      },
      {
        id: "u-2",
        email: "b@test",
        handle: "b",
        display_name: "Beta",
        role: "user",
        created_at: null,
      },
    ];
    const { getByTestId } = render(<AdminUsersPage />);
    expect(getByTestId("stub-cell-0-display_name")).toHaveTextContent("—");
    expect(getByTestId("stub-cell-1-display_name")).toHaveTextContent("Beta");
  });

  it("created_at cell falls back to '—' when null and formats when present", () => {
    queryState.data = [
      {
        id: "u-1",
        email: "a@test",
        handle: "a",
        display_name: null,
        role: "user",
        created_at: null,
      },
      {
        id: "u-2",
        email: "b@test",
        handle: "b",
        display_name: null,
        role: "user",
        created_at: "2026-01-01T00:00:00Z",
      },
    ];
    const { getByTestId } = render(<AdminUsersPage />);
    expect(getByTestId("stub-cell-0-created_at")).toHaveTextContent("—");
    // formatRelative returns a non-empty human string; assert it isn't the
    // null-fallback dash. The exact value depends on system clock, so check
    // non-emptiness + dash exclusion rather than pinning a literal.
    const cell1 = getByTestId("stub-cell-1-created_at").textContent ?? "";
    expect(cell1.length).toBeGreaterThan(0);
    expect(cell1).not.toBe("—");
  });

  it("role cell mounts a RoleCell whose Select trigger carries the user email", () => {
    queryState.data = [
      {
        id: "u-other",
        email: "other@test",
        handle: "other",
        display_name: null,
        role: "user",
        created_at: null,
      },
    ];
    const { getByLabelText } = render(<AdminUsersPage />);
    expect(getByLabelText("Role for other@test")).toBeInTheDocument();
  });

  it("passes selfId=null to RoleCell when useAuth.currentUser is null (L106 nullish fallback)", () => {
    // Source: `<RoleCell ... selfId={currentUser?.id ?? null} />` — the
    // `?? null` arm fires before useAuth's first /users/me resolution
    // (currentUser is null until the auth context boots). Pin the
    // fallback so a refactor that drops it can't crash the page with a
    // selfId=undefined TS error or surface a stale `you` badge.
    authState.currentUser = null;
    queryState.data = [
      {
        id: "u-other",
        email: "other@test",
        handle: "other",
        display_name: null,
        role: "user",
        created_at: null,
      },
    ];
    const { getByLabelText, queryByText } = render(<AdminUsersPage />);
    // RoleCell still mounts (the cell renderer doesn't gate on selfId).
    expect(getByLabelText("Role for other@test")).toBeInTheDocument();
    // The email-column "you" badge (L87 `row.original.id === currentUser?.id`)
    // must NOT render when currentUser is null — verifies the nullish
    // chain doesn't accidentally match `undefined === undefined`.
    expect(queryByText(/^you$/i)).toBeNull();
  });
});

describe("_authed.admin.users.tsx (RoleCell)", () => {
  function makeUser(overrides: Partial<User> = {}): User {
    return {
      id: "u-other",
      email: "other@test",
      handle: "other",
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

  it("selecting a non-self user's new role triggers useUpdateUserRole.mutate", async () => {
    // Pins the onValueChange handler wiring: a Select-option click must
    // call mutate with {userId, role}. Without this, a refactor that
    // drops the handler (or mismatches the payload keys) would still
    // ship green because the render-only tests above don't exercise it.
    const userEvent = (await import("@testing-library/user-event")).default;
    const { getByLabelText, getByRole } = render(
      <RoleCell user={makeUser({ id: "u-other" })} selfId="u-self" />,
    );
    await userEvent.setup().click(getByLabelText("Role for other@test"));
    await userEvent.setup().click(getByRole("option", { name: "developer" }));
    expect(mutateMock).toHaveBeenCalledTimes(1);
    expect(mutateMock).toHaveBeenCalledWith({
      userId: "u-other",
      role: "developer",
    });
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
