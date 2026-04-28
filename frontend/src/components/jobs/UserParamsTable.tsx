import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface Props {
  userParams: Record<string, unknown>;
  defaults: Record<string, unknown> | null;
}

export function UserParamsTable({ userParams, defaults }: Props) {
  const keys = Object.keys(userParams).sort();
  if (keys.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No hyperparameters submitted (used detector defaults).
      </p>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Parameter</TableHead>
          <TableHead>Your value</TableHead>
          {defaults && <TableHead>Default</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {keys.map((k) => {
          const userVal = userParams[k];
          const defaultVal = defaults?.[k];
          const isDefault =
            defaults != null &&
            JSON.stringify(userVal) === JSON.stringify(defaultVal);
          return (
            <TableRow key={k}>
              <TableCell className="font-mono">{k}</TableCell>
              <TableCell
                className={isDefault ? "text-muted-foreground" : "font-medium"}
              >
                {JSON.stringify(userVal)}
                {isDefault && <span className="ml-2 text-xs">(default)</span>}
              </TableCell>
              {defaults && (
                <TableCell className="font-mono text-muted-foreground">
                  {defaultVal !== undefined ? JSON.stringify(defaultVal) : "—"}
                </TableCell>
              )}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
