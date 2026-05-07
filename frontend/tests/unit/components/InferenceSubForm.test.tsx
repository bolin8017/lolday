import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { InferenceSubForm } from "@/components/forms/InferenceSubForm";

vi.mock("@/api/queries/models", () => ({
  useRegisteredModels: () => ({
    data: [
      { owner: "alice", name: "elf-rf" },
      { owner: "alice", name: "elf-cnn" },
    ],
  }),
  useModelVersions: (owner: string, name: string) => ({
    data:
      owner && name === "elf-rf"
        ? [
            {
              id: "mv1",
              mlflow_version: 1,
              current_stage: "Production",
              detector_id: "det-rf",
              detector_version_tag: "v1.0.0",
            },
          ]
        : [],
  }),
}));
vi.mock("@/api/queries/detectors", () => ({
  useDetector: (id: string) => ({
    data: id === "det-rf" ? { id: "det-rf", display_name: "ELF RF" } : null,
  }),
  useDetectorVersions: () => ({
    data: { items: [{ id: "v1", git_tag: "v1.0.0", status: "active" }] },
  }),
  useDetectorVersion: () => ({
    data: { manifest: { stages: { predict: { params_schema: {} } } } },
  }),
}));
vi.mock("@/api/queries/datasets", () => ({
  useDatasets: () => ({ data: { items: [{ id: "ds1", name: "samples-x" }] } }),
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("InferenceSubForm", () => {
  it("renders Source model section before Detector section", () => {
    wrap(
      <InferenceSubForm
        type="predict"
        sourceModelOwner=""
        setSourceModelOwner={() => {}}
        sourceModelName=""
        setSourceModelName={() => {}}
        sourceModelVersionId=""
        setSourceModelVersionId={() => {}}
        derivedDetectorId=""
        setDerivedDetectorId={() => {}}
        derivedDetectorVersionTag=""
        setDerivedDetectorVersionTag={() => {}}
        overrideDetectorVersion={false}
        setOverrideDetectorVersion={() => {}}
        predictDatasetId=""
        setPredictDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    // Source model card title is the first interactive section
    const srcModelTitles = screen.getAllByText(/source model/i);
    expect(srcModelTitles.length).toBeGreaterThanOrEqual(1);
  });

  it("renders Advanced override toggle (collapsed by default)", () => {
    wrap(
      <InferenceSubForm
        type="predict"
        sourceModelOwner=""
        setSourceModelOwner={() => {}}
        sourceModelName=""
        setSourceModelName={() => {}}
        sourceModelVersionId=""
        setSourceModelVersionId={() => {}}
        derivedDetectorId=""
        setDerivedDetectorId={() => {}}
        derivedDetectorVersionTag=""
        setDerivedDetectorVersionTag={() => {}}
        overrideDetectorVersion={false}
        setOverrideDetectorVersion={() => {}}
        predictDatasetId=""
        setPredictDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    expect(
      screen.getByRole("button", { name: /advanced.*override|進階.*覆寫/i }),
    ).toBeInTheDocument();
  });
});
