import { useEffect, useRef } from "react";
import { cn } from "@/lib/cn";

interface Props {
  text: string;
  className?: string;
}

export function LogTail({ text, className }: Props) {
  const ref = useRef<HTMLPreElement | null>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [text]);
  return (
    <pre
      ref={ref}
      className={cn(
        // Logs render in a fixed terminal-style dark theme regardless of
        // app theme. zinc-950/100 is intentional (the rest of the chrome
        // uses theme-aware semantic tokens; logs are visually a "console"
        // surface, not a card).
        "max-h-[480px] overflow-auto rounded-md bg-zinc-950 p-3 font-mono text-xs text-zinc-100",
        className,
      )}
    >
      {text || "(no output)"}
    </pre>
  );
}
