import {
  useAdminUsers,
  useUpdateUserRole,
  type Role,
  type User,
} from "@/api/queries/admin";
import { useAuth } from "@/hooks/useAuth";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { formatRelative } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Admin / Users" };

const ROLES: Role[] = ["user", "developer", "admin"];

function RoleCell({ user, selfId }: { user: User; selfId: string | null }) {
  const mut = useUpdateUserRole();
  const isSelf = selfId === user.id;
  const demotingSelf = isSelf;
  return (
    <Select
      value={user.role}
      disabled={mut.isPending}
      onValueChange={(v) => mut.mutate({ userId: user.id, role: v as Role })}
    >
      <SelectTrigger className="w-36">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {ROLES.map((r) => (
          <SelectItem
            key={r}
            value={r}
            disabled={demotingSelf && r !== "admin"}
          >
            {r}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export default function AdminUsersPage() {
  const { currentUser } = useAuth();
  const { data, isLoading, isError, error } = useAdminUsers();

  const errorStatus = (error as { status?: number } | undefined)?.status;

  if (isError && errorStatus === 403) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold">Users</h1>
        <p className="text-sm text-muted-foreground">
          Your account does not have admin permission. Ask the lolday operator
          to upgrade your role (admin → <code>/admin/users</code>).
        </p>
      </div>
    );
  }

  const users = data ?? [];
  const columns: ColumnDef<User>[] = [
    {
      accessorKey: "email",
      header: "Email",
      cell: ({ row }) => (
        <span>
          {row.original.email}
          {row.original.id === currentUser?.id && (
            <Badge variant="secondary" className="ml-2">
              you
            </Badge>
          )}
        </span>
      ),
      meta: { cardSlot: "title" },
    },
    {
      accessorKey: "display_name",
      header: "Display name",
      cell: ({ row }) => row.original.display_name ?? "—",
      meta: { cardLabel: "Display name", cardSlot: "body" },
    },
    {
      accessorKey: "role",
      header: "Role",
      cell: ({ row }) => (
        <RoleCell user={row.original} selfId={currentUser?.id ?? null} />
      ),
      meta: { cardLabel: "Role", cardSlot: "body" },
    },
    {
      accessorKey: "created_at",
      header: "Created",
      cell: ({ row }) =>
        row.original.created_at ? formatRelative(row.original.created_at) : "—",
      meta: { cardLabel: "Created", cardSlot: "body" },
    },
  ];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Users"
        description={
          <>
            Promote lab members to <code>developer</code> (register detectors,
            submit jobs) or <code>admin</code> (full access). New SSO arrivals
            default to <code>user</code>.
          </>
        }
      />
      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : (
        <DataTable columns={columns} data={users} />
      )}
    </div>
  );
}
