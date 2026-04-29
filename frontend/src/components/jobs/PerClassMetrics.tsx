import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface ClassMetric {
  precision: number;
  recall: number;
  f1: number;
  support: number;
}

interface Props {
  perClass: Record<string, ClassMetric>;
  positiveClass?: string;
}

export function PerClassMetrics({ perClass, positiveClass }: Props) {
  const rows = Object.entries(perClass).sort(([a], [b]) => {
    if (a === positiveClass) return -1;
    if (b === positiveClass) return 1;
    return a.localeCompare(b);
  });
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Class</TableHead>
          <TableHead className="text-right">Precision</TableHead>
          <TableHead className="text-right">Recall</TableHead>
          <TableHead className="text-right">F1</TableHead>
          <TableHead className="text-right">Support</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map(([cls, m]) => (
          <TableRow
            key={cls}
            className={cls === positiveClass ? "font-medium" : ""}
          >
            <TableCell>
              {cls}
              {cls === positiveClass ? " (positive)" : ""}
            </TableCell>
            <TableCell className="text-right">
              {m.precision.toFixed(4)}
            </TableCell>
            <TableCell className="text-right">{m.recall.toFixed(4)}</TableCell>
            <TableCell className="text-right">{m.f1.toFixed(4)}</TableCell>
            <TableCell className="text-right">{m.support}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
