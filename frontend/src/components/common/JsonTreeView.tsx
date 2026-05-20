import { useState } from "react";
import {
  JsonView,
  darkStyles,
  defaultStyles,
  type Props as JsonViewProps,
} from "react-json-view-lite";
import "react-json-view-lite/dist/index.css";
import { Check, Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useResolvedTheme } from "@/hooks/useResolvedTheme";

interface Props {
  value: unknown;
  collapsed?: number | boolean;
  copyable?: boolean;
}

export function JsonTreeView({ value, collapsed = 1, copyable = true }: Props) {
  const theme = useResolvedTheme();
  const [copied, setCopied] = useState(false);

  const data: object | unknown[] =
    value && typeof value === "object" ? (value as object | unknown[]) : {};

  const shouldExpandNode: JsonViewProps["shouldExpandNode"] = (level) => {
    if (typeof collapsed === "boolean") return !collapsed;
    return level < collapsed;
  };

  // Library's darkStyles.container hard-codes background rgb(0, 43, 54);
  // drop it so the wrapper's bg-card (shadcn token) shows through.
  const style =
    theme === "dark" ? { ...darkStyles, container: "" } : defaultStyles;

  const handleCopy = async () => {
    if (!navigator.clipboard) return;
    await navigator.clipboard.writeText(JSON.stringify(value, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      data-testid="json-tree-view"
      data-theme={theme}
      className="relative overflow-auto rounded-md border bg-card p-3 font-mono text-xs"
    >
      {copyable && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={handleCopy}
          aria-label={copied ? "Copied" : "Copy JSON to clipboard"}
          className="absolute right-2 top-2 h-7 w-7"
        >
          {copied ? (
            <Check className="h-4 w-4" />
          ) : (
            <Copy className="h-4 w-4" />
          )}
        </Button>
      )}
      <JsonView data={data} style={style} shouldExpandNode={shouldExpandNode} />
    </div>
  );
}
