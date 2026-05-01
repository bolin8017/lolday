import { useEffect } from "react";
import { Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface Props {
  experimentId: string;
  availableMetrics: string[];
  availableParams: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
}

export function RunsColumnPicker({
  experimentId,
  availableMetrics,
  availableParams,
  selected,
  onChange,
}: Props) {
  useEffect(() => {
    localStorage.setItem(
      `runs.columns.${experimentId}`,
      JSON.stringify(selected),
    );
  }, [experimentId, selected]);

  function toggle(key: string) {
    if (selected.includes(key)) onChange(selected.filter((s) => s !== key));
    else onChange([...selected, key]);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          <Settings2 className="mr-2 h-4 w-4" />
          Columns ({selected.length})
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-96 overflow-y-auto">
        <DropdownMenuLabel>Metrics</DropdownMenuLabel>
        {availableMetrics.map((m) => {
          const key = `metrics.${m}`;
          return (
            <DropdownMenuCheckboxItem
              key={key}
              checked={selected.includes(key)}
              onCheckedChange={() => toggle(key)}
            >
              {m}
            </DropdownMenuCheckboxItem>
          );
        })}
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Parameters</DropdownMenuLabel>
        {availableParams.map((p) => {
          const key = `params.${p}`;
          return (
            <DropdownMenuCheckboxItem
              key={key}
              checked={selected.includes(key)}
              onCheckedChange={() => toggle(key)}
            >
              {p}
            </DropdownMenuCheckboxItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function loadColumnsFromStorage(
  experimentId: string,
  fallback: string[],
): string[] {
  try {
    const raw = localStorage.getItem(`runs.columns.${experimentId}`);
    if (!raw) return fallback;
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}
