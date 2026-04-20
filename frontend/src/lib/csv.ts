export interface CsvPreview {
  columns: string[];
  rows: Record<string, string>[];
  totalRows: number;
}

const REQUIRED = ["file_name", "label"];

export function parseCsvPreview(text: string, limit = 20): CsvPreview {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length === 0) throw new Error("Empty CSV");
  const columns = splitLine(lines[0]);
  for (const req of REQUIRED) {
    if (!columns.includes(req)) throw new Error(`Missing required column: ${req}`);
  }
  const dataLines = lines.slice(1);
  const rows = dataLines.slice(0, limit).map((line) => {
    const cells = splitLine(line);
    return Object.fromEntries(columns.map((c, i) => [c, cells[i] ?? ""]));
  });
  return { columns, rows, totalRows: dataLines.length };
}

// RFC 4180 minimal — handles quoted fields with commas/quotes.
function splitLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuote) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') inQuote = false;
      else cur += ch;
    } else {
      if (ch === ",") { out.push(cur); cur = ""; }
      else if (ch === '"') inQuote = true;
      else cur += ch;
    }
  }
  out.push(cur);
  return out;
}
