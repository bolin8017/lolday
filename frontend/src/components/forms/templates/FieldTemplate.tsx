import { RotateCcw } from "lucide-react";
import type { FieldTemplateProps } from "@rjsf/utils";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

interface ResetCtx {
  onResetField?: (id: string) => void;
}

export function FieldTemplate(props: FieldTemplateProps) {
  const {
    id,
    classNames,
    label,
    required,
    children,
    description,
    schema,
    formData,
    displayLabel,
    formContext,
  } = props;

  const ctx = (formContext ?? {}) as ResetCtx;
  const defaultValue = (schema as { default?: unknown }).default;
  const isModified = defaultValue !== undefined && formData !== defaultValue;
  const showReset = isModified && typeof ctx.onResetField === "function";

  return (
    <div className={`mb-4 ${classNames ?? ""}`}>
      {displayLabel && (
        <div className="mb-1 flex items-center gap-2">
          <Label htmlFor={id} className="font-medium">
            {label}
            {required && <span className="ml-1 text-destructive">*</span>}
          </Label>
          {defaultValue !== undefined && !isModified && (
            <Badge
              variant="outline"
              className="text-muted-foreground text-xs font-normal"
            >
              default {JSON.stringify(defaultValue)}
            </Badge>
          )}
          {isModified && (
            <Badge variant="default" className="text-xs font-normal">
              modified
            </Badge>
          )}
          {showReset && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => ctx.onResetField!(id)}
              className="ml-auto h-6 px-2 text-xs text-muted-foreground"
            >
              <RotateCcw className="mr-1 h-3 w-3" />
              reset
            </Button>
          )}
        </div>
      )}
      <div>{children}</div>
      {description}
    </div>
  );
}
