import { useState } from "react";
import { ChevronDown, ChevronRight, Copy, Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Dataset } from "@/api/queries/datasets";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Button } from "@/components/ui/button";

interface Props {
  dataset: Dataset;
}

export function DatasetMetadataDetails({ dataset }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {t("datasets.detail.metadata")}
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-2 rounded border p-3 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">
            {t("datasets.detail.owner")}:
          </span>
          <code className="font-mono text-xs">{dataset.owner_id}</code>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">
            {t("datasets.detail.checksum")}:
          </span>
          <code className="break-all font-mono text-xs">
            {dataset.csv_checksum}
          </code>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 gap-1 px-2"
            aria-label={copied ? t("common.copied") : t("common.copy")}
            onClick={async () => {
              await navigator.clipboard.writeText(dataset.csv_checksum);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? t("common.copied") : t("common.copy")}
          </Button>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
