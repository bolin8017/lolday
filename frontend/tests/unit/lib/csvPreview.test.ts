import { describe, it, expect } from "vitest";
import { parseCsvPreview } from "@/lib/csv";

const HEX = "a".repeat(64);
const HEX2 = "b".repeat(64);

describe("parseCsvPreview", () => {
  it("returns rows when CSV is valid (existing behaviour)", () => {
    const csv = `file_name,label\n${HEX},Malware\n${HEX2},Benign\n`;
    const out = parseCsvPreview(csv, 5);
    expect(out.totalRows).toBe(2);
    expect(out.columns).toEqual(["file_name", "label"]);
  });

  it("rejects missing required columns", () => {
    expect(() => parseCsvPreview("a,b\n1,2\n", 5)).toThrowError(
      /Missing required column/,
    );
  });

  it("rejects when file_name is not a 64-char lowercase hex SHA256", () => {
    const csv = `file_name,label\nDEADBEEF,Malware\n`;
    expect(() => parseCsvPreview(csv, 5)).toThrowError(/SHA256/i);
  });

  it("rejects when label is not Malware or Benign", () => {
    const csv = `file_name,label\n${HEX},Suspicious\n`;
    expect(() => parseCsvPreview(csv, 5)).toThrowError(
      /label must be Malware or Benign/,
    );
  });

  it("accepts family on Malware rows", () => {
    const csv = `file_name,label,family\n${HEX},Malware,mirai\n`;
    const out = parseCsvPreview(csv, 5);
    expect(out.totalRows).toBe(1);
  });

  it("rejects when there are no data rows", () => {
    expect(() => parseCsvPreview("file_name,label\n", 5)).toThrowError(
      /no data rows/i,
    );
  });

  it("includes the row number in error messages", () => {
    const csv = `file_name,label\n${HEX},Malware\nbadhash,Benign\n`;
    expect(() => parseCsvPreview(csv, 5)).toThrowError(/Row 3/);
  });

  // ----- splitLine + line-ending branch coverage -----

  it("accepts CRLF line endings", () => {
    // Windows-saved CSVs use \r\n; the regex split is `/\r?\n/` so both
    // line endings must produce identical row counts.
    const csv = `file_name,label\r\n${HEX},Malware\r\n${HEX2},Benign\r\n`;
    const out = parseCsvPreview(csv, 5);
    expect(out.totalRows).toBe(2);
    expect(out.columns).toEqual(["file_name", "label"]);
  });

  it("tolerates blank lines between data rows", () => {
    // A double-newline in the middle of the file (common when copy-pasted
    // through a chat client) must not abort with a misleading "got: (empty)"
    // SHA256 error — the lines.slice(1).filter(l => l.length > 0) gate
    // is what protects this; the test pins that protection.
    const csv = `file_name,label\n${HEX},Malware\n\n\n${HEX2},Benign\n`;
    const out = parseCsvPreview(csv, 5);
    expect(out.totalRows).toBe(2);
  });

  it("respects double quotes around fields containing commas", () => {
    // splitLine is RFC-4180-minimal: a comma inside a "…" quoted field
    // must NOT split the row. Detector CSVs may grow comma-bearing
    // columns (e.g. tag lists) — pin the behaviour now.
    const csv = `file_name,label,note\n${HEX},Malware,"hello, world"\n`;
    const out = parseCsvPreview(csv, 5);
    expect(out.rows[0]).toEqual({
      file_name: HEX,
      label: "Malware",
      note: "hello, world",
    });
  });

  it("unescapes doubled quotes inside a quoted field", () => {
    // RFC 4180: ""…"" inside a quoted field decodes to a single ".
    // The splitLine inner branch (`ch === '"' && line[i+1] === '"'`) is
    // what implements this — keep it covered so a future refactor
    // doesn't break import of CSVs that quote literal quotes.
    const csv = `file_name,label,note\n${HEX},Malware,"a""b"\n`;
    const out = parseCsvPreview(csv, 5);
    expect(out.rows[0]?.note).toBe('a"b');
  });

  it("treats a trailing empty cell as an empty string column value", () => {
    // No quoting, just a trailing comma — the splitLine ``out.push(cur)``
    // tail must produce a third empty cell so the row stays
    // well-shaped against the columns header.
    const csv = `file_name,label,note\n${HEX},Malware,\n`;
    const out = parseCsvPreview(csv, 5);
    expect(out.rows[0]?.note).toBe("");
  });
});
