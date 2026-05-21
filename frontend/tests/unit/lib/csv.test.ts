import { describe, it, expect } from "vitest";
import { parseCsvPreview } from "@/lib/csv";

// Valid 64-char lowercase hex SHA256 stubs for test data
const SHA = (ch: string) => ch.repeat(64);

describe("parseCsvPreview", () => {
  it("parses header + rows", () => {
    const csv = `file_name,label\n${SHA("a")},Malware\n${SHA("b")},Benign\n`;
    const p = parseCsvPreview(csv);
    expect(p.columns).toEqual(["file_name", "label"]);
    expect(p.rows.length).toBe(2);
    expect(p.rows[0]).toEqual({ file_name: SHA("a"), label: "Malware" });
    expect(p.totalRows).toBe(2);
  });

  it("caps rows at limit", () => {
    // 50 unique hashes via 2-digit hex suffix
    const rows = Array.from(
      { length: 50 },
      (_, i) => `${"a".repeat(62)}${i.toString(16).padStart(2, "0")},Malware`,
    ).join("\n");
    const csv = `file_name,label\n${rows}\n`;
    const p = parseCsvPreview(csv, 20);
    expect(p.rows.length).toBe(20);
    expect(p.totalRows).toBe(50);
  });

  it("rejects missing required columns", () => {
    expect(() => parseCsvPreview("foo,bar\n1,2\n")).toThrow(/required/i);
  });

  it("rejects CSV with header only and no data rows", () => {
    expect(() => parseCsvPreview("file_name,label\n")).toThrow(/no data rows/i);
  });

  it("rejects a bad SHA256 with an actionable error including the row number", () => {
    const csv = `file_name,label\nshort,Malware\n`;
    expect(() => parseCsvPreview(csv)).toThrow(/Row 2/);
    expect(() => parseCsvPreview(csv)).toThrow(/file_name/);
  });

  it("emits '(empty)' in the error when the file_name cell is missing", () => {
    // Trailing comma with no value → empty file_name cell.
    const csv = `file_name,label\n,Malware\n`;
    expect(() => parseCsvPreview(csv)).toThrow(/\(empty\)/);
  });

  it("rejects a non-{Malware,Benign} label with an actionable error", () => {
    const csv = `file_name,label\n${SHA("a")},Unknown\n`;
    expect(() => parseCsvPreview(csv)).toThrow(/Row 2.*Unknown/);
  });

  it("emits '(empty)' in the error when the label cell is missing", () => {
    const csv = `file_name,label\n${SHA("a")},\n`;
    expect(() => parseCsvPreview(csv)).toThrow(/label.*\(empty\)/);
  });

  it("splits quoted fields containing commas (RFC 4180 minimal)", () => {
    // Add a third column to exercise the quoted-cell branch; required cols
    // (file_name, label) stay structurally first to keep validation happy.
    const csv = `file_name,label,note\n${SHA("a")},Malware,"hello, world"\n`;
    const p = parseCsvPreview(csv);
    expect(p.columns).toEqual(["file_name", "label", "note"]);
    expect(p.rows[0]).toEqual({
      file_name: SHA("a"),
      label: "Malware",
      note: "hello, world",
    });
  });

  it("decodes doubled-quotes inside a quoted field as a single quote", () => {
    const csv = `file_name,label,note\n${SHA("a")},Malware,"He said ""hi"""\n`;
    const p = parseCsvPreview(csv);
    expect(p.rows[0].note).toBe('He said "hi"');
  });

  it("accepts CRLF line endings (Windows-saved CSVs)", () => {
    const csv = `file_name,label\r\n${SHA("a")},Malware\r\n${SHA("b")},Benign\r\n`;
    const p = parseCsvPreview(csv);
    expect(p.totalRows).toBe(2);
  });

  it("backfills missing trailing cells with an empty string", () => {
    // Header has 3 cols; row only has 2 — the third cell is undefined and
    // the parser must fall back to "" rather than emit `undefined`.
    const csv = `file_name,label,note\n${SHA("a")},Malware\n`;
    const p = parseCsvPreview(csv);
    expect(p.rows[0]).toEqual({
      file_name: SHA("a"),
      label: "Malware",
      note: "",
    });
  });
});
