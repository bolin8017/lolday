import { describe, it, expect } from "vitest";
import { checkCsvSize, MAX_CSV_BYTES } from "@/components/forms/DatasetUploadForm.logic";

describe("checkCsvSize", () => {
  it("accepts small CSV", () => {
    expect(checkCsvSize("a,b\n1,2\n")).toBeNull();
  });
  it("rejects > 10 MB", () => {
    const oversize = "a,b\n" + "x,y\n".repeat(Math.ceil(MAX_CSV_BYTES / 4));
    expect(checkCsvSize(oversize)).toMatch(/exceeds/i);
  });
});
