import { HelpCircle } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface Props {
  children: ReactNode;
  /**
   * Use Popover (click-to-open, larger surface) instead of Tooltip
   * (hover-to-open, single-line). Pick popover for content longer
   * than two lines or with formatting.
   */
  popover?: boolean;
  className?: string;
}

/**
 * Small "?" icon next to a label that surfaces a short hint or
 * a longer explanation. Reusable, mainstream pattern (Material 3,
 * Carbon, Atlassian).
 */
export function HelpHint({ children, popover = false, className }: Props) {
  if (popover) {
    return (
      <Popover>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Help"
            className={className ?? "h-6 w-6"}
          >
            <HelpCircle className="h-4 w-4 text-muted-foreground" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="max-w-xs text-sm">{children}</PopoverContent>
      </Popover>
    );
  }
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Help"
            className={className ?? "h-6 w-6"}
          >
            <HelpCircle className="h-4 w-4 text-muted-foreground" />
          </Button>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs text-sm">{children}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
