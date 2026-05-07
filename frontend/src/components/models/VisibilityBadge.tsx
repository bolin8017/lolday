import { Globe, Lock } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";

interface Props {
  visibility: "public" | "private";
  iconOnly?: boolean;
}

export function VisibilityBadge({ visibility, iconOnly }: Props) {
  const { t } = useTranslation();
  const isPublic = visibility === "public";

  const Icon = isPublic ? Globe : Lock;
  const label = isPublic
    ? t("models.visibility.public")
    : t("models.visibility.private");
  const colorClass = isPublic
    ? "border-emerald-500 text-emerald-700 dark:text-emerald-400"
    : "border-slate-400 text-slate-600 dark:text-slate-400";

  return (
    <Badge variant="outline" className={`gap-1 ${colorClass}`}>
      <Icon aria-label={isPublic ? "globe" : "lock"} className="h-3 w-3" />
      {!iconOnly && label}
    </Badge>
  );
}
