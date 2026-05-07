import { X } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";

interface Props {
  value: string;
  onValueChange: (value: string) => void;
  clearable?: boolean;
  disabled?: boolean;
  children: ReactNode;
}

/**
 * shadcn Select wrapper that adds a "clear" button (X icon) when
 * the field has a value and `clearable` is true. Used for optional
 * fields where Radix Select alone provides no way to deselect.
 */
export function ClearableSelect({
  value,
  onValueChange,
  clearable = false,
  disabled = false,
  children,
}: Props) {
  const showClear = clearable && !!value && !disabled;
  return (
    <div className="flex items-center gap-1">
      <div className="flex-1">
        <Select value={value} onValueChange={onValueChange} disabled={disabled}>
          {children}
        </Select>
      </div>
      {showClear && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Clear"
          onClick={() => onValueChange("")}
        >
          <X className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
}
