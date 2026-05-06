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

  it("rejects when family is set on a Benign row", () => {
    const csv = `file_name,label,family\n${HEX},Benign,mirai\n`;
    expect(() => parseCsvPreview(csv, 5)).toThrowError(/family.*Malware/i);
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
});
