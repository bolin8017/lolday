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
});
