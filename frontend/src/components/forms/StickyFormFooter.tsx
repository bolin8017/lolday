import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

interface Props {
  children: ReactNode;
  className?: string;
}

/**
 * Sticky-bottom CTA bar for forms on mobile, used by `_authed.*.new` form
 * pages and the profile sub-forms.
 *
 * Why the negative `-mx-*` and the safe-area calc:
 *
 * - The `-mx-4 sm:-mx-6` cancels its parent's `p-4 sm:p-6` padding so the
 *   bar visually spans edge-to-edge inside the parent (Linear / Vercel /
 *   GitHub mobile-form convention). The breakpoints MUST match the parent's
 *   padding cadence — the post-PR-3 main shell uses `p-4 sm:p-6` and shadcn
 *   `Card` uses `p-4 sm:p-6`, so this is a safe default for both.
 * - `pb-[calc(0.75rem+env(safe-area-inset-bottom))]` clears the iOS home
 *   indicator inset on iPhone with notch / dynamic island. On Android the
 *   inset resolves to 0 px so the calc reduces to `0.75rem`.
 *
 * Do NOT render `<StickyFormFooter>` inside a Dialog — Dialogs have their
 * own `p-6` constant padding and the negative `-mx-4 sm:-mx-6` produces a
 * mismatched bar that overflows the dialog box.
 */
export function StickyFormFooter({ children, className }: Props) {
  return (
    <div
      className={cn(
        "sticky bottom-0 -mx-4 flex justify-end gap-2 border-t bg-background px-4 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:-mx-6 sm:px-6 sm:pb-3",
        className,
      )}
    >
      {children}
    </div>
  );
}
