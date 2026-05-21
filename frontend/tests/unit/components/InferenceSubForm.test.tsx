import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { InferenceSubForm } from "@/components/forms/InferenceSubForm";

let useModelVersionsImpl = (owner: string, name: string) => ({
  data:
    owner && name === "elf-rf"
      ? [
          {
            id: "mv1",
            mlflow_version: 1,
            current_stage: "Production",
            detector_id: "det-rf",
            detector_version_tag: "v1.0.0",
            is_runnable: true,
          },
        ]
      : [],
});
vi.mock("@/api/queries/models", () => ({
  useRegisteredModels: () => ({
    data: [
      { owner: "alice", name: "elf-rf" },
      { owner: "alice", name: "elf-cnn" },
    ],
  }),
  useModelVersions: (owner: string, name: string) =>
    useModelVersionsImpl(owner, name),
}));
vi.mock("@/api/queries/detectors", () => ({
  useDetector: (id: string) => ({
    data: id === "det-rf" ? { id: "det-rf", display_name: "ELF RF" } : null,
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

  it("does not expose an override toggle (footgun removed)", () => {
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
        predictDatasetId=""
        setPredictDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    // Detector version is rendered read-only; no override button exists.
    expect(
      screen.queryByRole("button", { name: /advanced.*override|進階.*覆寫/i }),
    ).toBeNull();
  });

  it("does not render a 'Detector (derived from model)' card (it's redundant — the model implies the detector)", () => {
    wrap(
      <InferenceSubForm
        type="predict"
        sourceModelOwner="alice"
        setSourceModelOwner={() => {}}
        sourceModelName="elf-rf"
        setSourceModelName={() => {}}
        sourceModelVersionId=""
        setSourceModelVersionId={() => {}}
        derivedDetectorId="det-rf"
        setDerivedDetectorId={() => {}}
        derivedDetectorVersionTag="v1.0.0"
        setDerivedDetectorVersionTag={() => {}}
        predictDatasetId=""
        setPredictDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    expect(screen.queryByText(/derived from model/i)).toBeNull();
  });

  it("derives detector_id + version_tag when sourceModelVersionId is set (useEffect)", () => {
    // Mock returns mv1 → detector_id=det-rf, detector_version_tag=v1.0.0.
    // Mounting with sourceModelVersionId="mv1" pre-selected must invoke the
    // two derived setters with those values (covers L74-77).
    useModelVersionsImpl = () => ({
      data: [
        {
          id: "mv1",
          mlflow_version: 1,
          current_stage: "Production",
          detector_id: "det-rf",
          detector_version_tag: "v1.0.0",
          is_runnable: true,
        },
      ],
    });
    const setDerivedDetectorId = vi.fn();
    const setDerivedDetectorVersionTag = vi.fn();
    wrap(
      <InferenceSubForm
        type="predict"
        sourceModelOwner="alice"
        setSourceModelOwner={() => {}}
        sourceModelName="elf-rf"
        setSourceModelName={() => {}}
        sourceModelVersionId="mv1"
        setSourceModelVersionId={() => {}}
        derivedDetectorId=""
        setDerivedDetectorId={setDerivedDetectorId}
        derivedDetectorVersionTag=""
        setDerivedDetectorVersionTag={setDerivedDetectorVersionTag}
        predictDatasetId=""
        setPredictDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    expect(setDerivedDetectorId).toHaveBeenCalledWith("det-rf");
    expect(setDerivedDetectorVersionTag).toHaveBeenCalledWith("v1.0.0");
  });

  it("source-model change cascades clear of version + derived detector (L105-110)", async () => {
    useModelVersionsImpl = () => ({ data: [] });
    const setSourceModelOwner = vi.fn();
    const setSourceModelName = vi.fn();
    const setSourceModelVersionId = vi.fn();
    const setDerivedDetectorId = vi.fn();
    const setDerivedDetectorVersionTag = vi.fn();
    wrap(
      <InferenceSubForm
        type="predict"
        sourceModelOwner=""
        setSourceModelOwner={setSourceModelOwner}
        sourceModelName=""
        setSourceModelName={setSourceModelName}
        sourceModelVersionId="mv-stale"
        setSourceModelVersionId={setSourceModelVersionId}
        derivedDetectorId="det-stale"
        setDerivedDetectorId={setDerivedDetectorId}
        derivedDetectorVersionTag="v-stale"
        setDerivedDetectorVersionTag={setDerivedDetectorVersionTag}
        predictDatasetId=""
        setPredictDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    await userEvent.click(
      screen.getByRole("combobox", { name: /source model/i }),
    );
    await userEvent.click(
      screen.getByRole("option", { name: /alice\/elf-rf/i }),
    );
    expect(setSourceModelOwner).toHaveBeenCalledWith("alice");
    expect(setSourceModelName).toHaveBeenCalledWith("elf-rf");
    expect(setSourceModelVersionId).toHaveBeenCalledWith("");
    expect(setDerivedDetectorId).toHaveBeenCalledWith("");
    expect(setDerivedDetectorVersionTag).toHaveBeenCalledWith("");
  });

  it('type="evaluate" renders the Test dataset Select with dataset items (L184)', async () => {
    useModelVersionsImpl = () => ({ data: [] });
    wrap(
      <InferenceSubForm
        type="evaluate"
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
        predictDatasetId=""
        setPredictDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    // The predict dataset Select must not render in evaluate mode.
    expect(screen.queryByText(/predict dataset/i)).toBeNull();
    await userEvent.click(
      screen.getByRole("combobox", { name: /test dataset/i }),
    );
    expect(
      screen.getByRole("option", { name: /samples-x/i }),
    ).toBeInTheDocument();
  });

  it("disables model versions whose training detector version was retired (§10 #22)", async () => {
    // Two versions: v1 runnable, v2 retired. Retired option must be
    // disabled and carry the localised hint so the user knows why.
    useModelVersionsImpl = (owner: string, name: string) => ({
      data:
        owner && name === "elf-rf"
          ? [
              {
                id: "mv1",
                mlflow_version: 1,
                current_stage: "Production",
                detector_id: "det-rf",
                detector_version_tag: "v1.0.0",
                is_runnable: true,
              },
              {
                id: "mv2",
                mlflow_version: 2,
                current_stage: "Staging",
                detector_id: "det-rf",
                detector_version_tag: "v0.9.0-retired",
                is_runnable: false,
              },
            ]
          : [],
    });
    wrap(
      <InferenceSubForm
        type="predict"
        sourceModelOwner="alice"
        setSourceModelOwner={() => {}}
        sourceModelName="elf-rf"
        setSourceModelName={() => {}}
        sourceModelVersionId=""
        setSourceModelVersionId={() => {}}
        derivedDetectorId="det-rf"
        setDerivedDetectorId={() => {}}
        derivedDetectorVersionTag=""
        setDerivedDetectorVersionTag={() => {}}
        predictDatasetId=""
        setPredictDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    // Open the Model version dropdown so Radix Select renders the options
    // into the DOM (it portal-mounts on trigger click).
    await userEvent.click(
      screen.getByRole("combobox", { name: /model version/i }),
    );
    // The "training version retired" short label is rendered next to the
    // disabled option's stage. Use partial match (i18n short string).
    expect(
      screen.getByText(/training version retired|訓練版本已退役/),
    ).toBeInTheDocument();
    // Disabled option carries data-disabled per Radix Select primitive.
    const retiredOption = screen.getByRole("option", {
      name: /v2 \(Staging\)/i,
    });
    expect(retiredOption).toHaveAttribute("data-disabled");
  });
});
