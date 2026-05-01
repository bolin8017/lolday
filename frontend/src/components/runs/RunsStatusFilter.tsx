import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const RUNS_STATUSES = [
  "all",
  "FINISHED",
  "RUNNING",
  "FAILED",
  "SCHEDULED",
] as const;
export type RunsStatus = (typeof RUNS_STATUSES)[number];

export function isRunsStatus(v: unknown): v is RunsStatus {
  return (
    typeof v === "string" && (RUNS_STATUSES as readonly string[]).includes(v)
  );
}

interface Props {
  value: RunsStatus;
  onChange: (s: RunsStatus) => void;
}

export function RunsStatusFilter({ value, onChange }: Props) {
  return (
    <Select
      value={value}
      onValueChange={(v) => {
        if (isRunsStatus(v)) onChange(v);
      }}
    >
      <SelectTrigger className="w-36">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {RUNS_STATUSES.map((s) => (
          <SelectItem key={s} value={s}>
            {s === "all" ? "All statuses" : s}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
