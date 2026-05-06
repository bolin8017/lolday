export interface CsvPreview {
  columns: string[];
  rows: Record<string, string>[];
  totalRows: number;
}

const REQUIRED = ["file_name", "label"];
const SHA256_RE = /^[0-9a-f]{64}$/;
const VALID_LABELS = new Set(["Malware", "Benign"]);

export function parseCsvPreview(text: string, limit = 20): CsvPreview {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length === 0) throw new Error("Empty CSV");
  const columns = splitLine(lines[0]);
  for (const req of REQUIRED) {
    if (!columns.includes(req))
      throw new Error(`Missing required column: ${req}`);
  }
  const dataLines = lines.slice(1).filter((l) => l.length > 0);
  if (dataLines.length === 0) throw new Error("CSV has no data rows");

  const fileNameIdx = columns.indexOf("file_name");
  const labelIdx = columns.indexOf("label");
  const familyIdx = columns.indexOf("family"); // -1 if absent

  for (let i = 0; i < dataLines.length; i++) {
    const cells = splitLine(dataLines[i]);
    const rowNum = i + 2; // 1-indexed + header line
    const fileName = (cells[fileNameIdx] ?? "").trim();
    const label = (cells[labelIdx] ?? "").trim();

    if (!SHA256_RE.test(fileName)) {
      throw new Error(
        `Row ${rowNum}: file_name must be 64-char lowercase hex SHA256, got: ${fileName || "(empty)"}`,
      );
    }
    if (!VALID_LABELS.has(label)) {
      throw new Error(
        `Row ${rowNum}: label must be Malware or Benign, got: ${label || "(empty)"}`,
      );
    }
    if (familyIdx >= 0) {
      const family = (cells[familyIdx] ?? "").trim();
      if (family && label !== "Malware") {
        throw new Error(
          `Row ${rowNum}: family is only allowed on Malware rows, got: label=${label}`,
        );
      }
    }
  }

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
      if (ch === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') inQuote = false;
      else cur += ch;
    } else {
      if (ch === ",") {
        out.push(cur);
        cur = "";
      } else if (ch === '"') inQuote = true;
      else cur += ch;
    }
  }
  out.push(cur);
  return out;
}
