import { Fragment } from "react";
import { cn } from "@/lib/cn";

export type CellTone = "success" | "warn";

export function cellColor(
  _row: number,
  _col: number,
  onDiagonal: boolean,
): CellTone {
  return onDiagonal ? "success" : "warn";
}

const TONE_CLASSES: Record<CellTone, string> = {
  success: "bg-emerald-500 text-white",
  warn: "bg-rose-100 text-rose-900",
};

interface Props {
  labels: string[];
  matrix: number[][]; // row = true label, col = predicted
}

export function ConfusionMatrix({ labels, matrix }: Props) {
  return (
    <div className="overflow-x-auto">
      <div className="inline-block">
        <div
          className="grid gap-1"
          style={{
            gridTemplateColumns: `auto repeat(${labels.length}, minmax(4rem, 1fr))`,
          }}
        >
          <div />
          {labels.map((l) => (
            <div
              key={`col-${l}`}
              className="px-2 py-1 text-center text-xs font-medium text-muted-foreground"
            >
              Pred {l}
            </div>
          ))}
          {matrix.map((row, i) => (
            <Fragment key={`row-fragment-${i}`}>
              <div className="px-2 py-1 text-right text-xs font-medium text-muted-foreground">
                True {labels[i]}
              </div>
              {row.map((v, j) => {
                const tone = cellColor(i, j, i === j);
                return (
                  <div
                    key={`cell-${i}-${j}`}
                    className={cn(
                      "rounded px-3 py-2 text-center font-mono text-sm",
                      TONE_CLASSES[tone],
                    )}
                  >
                    {v}
                  </div>
                );
              })}
            </Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}
