import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DatasetKpiStrip } from "@/components/datasets/DatasetKpiStrip";
import type { Dataset } from "@/api/queries/datasets";

const baseDataset = {
  id: "00000000-0000-0000-0000-000000000000",
  name: "ds",
  description: null,
  owner_id: "11111111-1111-1111-1111-111111111111",
  visibility: "public",
  sample_count: 1548,
  label_distribution: { Malware: 912, Benign: 636 },
  family_distribution: { mirai: 234, dridex: 187 },
  size_bytes: 4096,
  csv_checksum: "deadbeef",
  created_at: new Date(Date.now() - 60_000).toISOString(),
} as unknown as Dataset;

describe("<DatasetKpiStrip>", () => {
  it("renders five tiles with formatted numbers", () => {
    render(<DatasetKpiStrip dataset={baseDataset} />);
    expect(screen.getByText("1,548")).toBeInTheDocument();
    expect(screen.getByText("912")).toBeInTheDocument();
    expect(screen.getByText("636")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument(); // families
    expect(screen.getByText(/seconds ago|minute/i)).toBeInTheDocument();
  });

  it("falls back to 0 when label/family distributions are missing", () => {
    const dataset = {
      ...baseDataset,
      label_distribution: {},
      family_distribution: null,
    } as unknown as Dataset;
    render(<DatasetKpiStrip dataset={dataset} />);
    const zeros = screen.getAllByText("0");
    // Malware = 0, Benign = 0, Families = 0
    expect(zeros.length).toBeGreaterThanOrEqual(3);
  });
});
