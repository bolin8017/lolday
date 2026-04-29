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
        "max-h-[480px] overflow-auto rounded-md bg-slate-950 p-3 font-mono text-xs text-slate-100",
        className,
      )}
    >
      {text || "(no output)"}
    </pre>
  );
}
