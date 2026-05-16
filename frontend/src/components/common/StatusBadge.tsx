import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { statusTone, type Tone } from "@/lib/status";
import { useTranslation } from "react-i18next";

const TONE_CLASSES: Record<Tone, string> = {
  success: "bg-emerald-100 text-emerald-700 hover:bg-emerald-100",
  destructive: "bg-red-100 text-red-700 hover:bg-red-100",
  info: "bg-sky-100 text-sky-700 hover:bg-sky-100",
  muted: "bg-slate-100 text-slate-700 hover:bg-slate-100",
  warning: "bg-amber-100 text-amber-700 hover:bg-amber-100",
};

export function StatusBadge({ status }: { status: string }) {
  const { t, i18n } = useTranslation();
  const key = `status.${status}`;
  const label = i18n.exists(key) ? t(key) : status;
  return (
    <Badge
      className={cn(TONE_CLASSES[statusTone(status)])}
      data-testid={`status-badge-${status}`}
    >
      {label}
    </Badge>
  );
}
