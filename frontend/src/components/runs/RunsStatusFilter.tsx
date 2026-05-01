import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const STATUSES = ["all", "FINISHED", "RUNNING", "FAILED", "SCHEDULED"] as const;
type Status = (typeof STATUSES)[number];

interface Props {
  value: Status;
  onChange: (s: Status) => void;
}

export function RunsStatusFilter({ value, onChange }: Props) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as Status)}>
      <SelectTrigger className="w-36">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {STATUSES.map((s) => (
          <SelectItem key={s} value={s}>
            {s === "all" ? "All statuses" : s}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export type { Status as RunsStatus };
