import { describe, it, expect } from "vitest";
import { parseCsvPreview } from "@/lib/csv";

describe("parseCsvPreview", () => {
  it("parses header + rows", () => {
    const csv = "file_name,label\nabc,Malware\ndef,Benign\n";
    const p = parseCsvPreview(csv);
    expect(p.columns).toEqual(["file_name", "label"]);
    expect(p.rows.length).toBe(2);
    expect(p.rows[0]).toEqual({ file_name: "abc", label: "Malware" });
    expect(p.totalRows).toBe(2);
  });

  it("caps rows at limit", () => {
    const rows = Array.from({ length: 50 }, (_, i) => `f${i},Malware`).join(
      "\n",
    );
    const csv = `file_name,label\n${rows}\n`;
    const p = parseCsvPreview(csv, 20);
    expect(p.rows.length).toBe(20);
    expect(p.totalRows).toBe(50);
  });

  it("rejects missing required columns", () => {
    expect(() => parseCsvPreview("foo,bar\n1,2\n")).toThrow(/required/i);
  });
});
