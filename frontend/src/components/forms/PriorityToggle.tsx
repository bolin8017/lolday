import { Zap } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

interface Props {
  value: 0 | 1;
  onChange: (next: 0 | 1) => void;
  disabled?: boolean;
  size?: "sm" | "default";
}

export function PriorityToggle({
  value,
  onChange,
  disabled,
  size = "default",
}: Props) {
  const { t } = useTranslation();
  const isHigh = value === 1;

  function set(next: 0 | 1) {
    if (next === value) return;
    onChange(next);
  }

  return (
    <div
      className={cn(
        "inline-flex rounded-md border bg-muted p-0.5",
        disabled && "opacity-60",
      )}
      role="group"
      aria-label={t("jobs.priority.label")}
    >
      <Button
        type="button"
        size={size === "sm" ? "sm" : "default"}
        variant="ghost"
        aria-pressed={!isHigh}
        disabled={disabled}
        onClick={() => set(0)}
        className={cn(
          "h-8 rounded-sm px-3",
          !isHigh && "bg-background shadow-sm",
        )}
      >
        {t("jobs.priority.normal")}
      </Button>
      <Button
        type="button"
        size={size === "sm" ? "sm" : "default"}
        variant="ghost"
        aria-pressed={isHigh}
        disabled={disabled}
        onClick={() => set(1)}
        className={cn(
          "h-8 rounded-sm px-3",
          isHigh &&
            "bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-300 shadow-sm",
        )}
      >
        <Zap className="mr-1 h-4 w-4" />
        {t("jobs.priority.high")}
      </Button>
    </div>
  );
}
