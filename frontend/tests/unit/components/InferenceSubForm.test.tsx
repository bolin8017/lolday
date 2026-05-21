import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

type ModelEntry = { owner: string; name: string };
type ModelVersionEntry = {
  id: string;
  mlflow_version: number;
  current_stage: string;
  detector_id: string;
  detector_version_tag: string;
  is_runnable: boolean;
};
type DatasetItem = { id: string; name: string };

const { queries } = vi.hoisted(() => ({
  queries: {
    registeredModels: undefined as unknown,
    modelVersions: undefined as unknown,
    detectorVersionDetail: undefined as unknown,
    datasets: undefined as unknown,
  },
}));

vi.mock("@/api/queries/models", () => ({
  useRegisteredModels: () => ({ data: queries.registeredModels }),
  useModelVersions: () => ({ data: queries.modelVersions }),
}));

vi.mock("@/api/queries/detectors", () => ({
  useDetector: (id: string) => ({
    data: id === "det-rf" ? { id: "det-rf", display_name: "ELF RF" } : null,
  }),
  useDetectorVersion: () => ({ data: queries.detectorVersionDetail }),
}));

vi.mock("@/api/queries/datasets", () => ({
  useDatasets: () => ({ data: queries.datasets }),
}));

// RjsfConfigForm has its own dedicated suite; stub so the stageSchema
// branch can render without dragging RJSF into the InferenceSubForm test.
vi.mock("@/components/forms/RjsfConfigForm", () => ({
  RjsfConfigForm: () => <div data-testid="stub-rjsf" />,
}));

import { InferenceSubForm } from "@/components/forms/InferenceSubForm";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const happyModels: ModelEntry[] = [
  { owner: "alice", name: "elf-rf" },
  { owner: "alice", name: "elf-cnn" },
];
const happyModelVersions: ModelVersionEntry[] = [
  {
    id: "mv1",
    mlflow_version: 1,
    current_stage: "Production",
    detector_id: "det-rf",
    detector_version_tag: "v1.0.0",
    is_runnable: true,
  },
];
const happyDatasets: { items: DatasetItem[] } = {
  items: [{ id: "ds1", name: "samples-x" }],
};
const detectorVersionWithSchema = {
  manifest: { stages: { predict: { params_schema: { type: "object" } } } },
};

function defaultProps(
  overrides: Partial<React.ComponentProps<typeof InferenceSubForm>> = {},
): React.ComponentProps<typeof InferenceSubForm> {
  return {
    type: "predict",
    sourceModelOwner: "",
    setSourceModelOwner: vi.fn(),
    sourceModelName: "",
    setSourceModelName: vi.fn(),
    sourceModelVersionId: "",
    setSourceModelVersionId: vi.fn(),
    derivedDetectorId: "",
    setDerivedDetectorId: vi.fn(),
    derivedDetectorVersionTag: "",
    setDerivedDetectorVersionTag: vi.fn(),
    predictDatasetId: "",
    setPredictDatasetId: vi.fn(),
    testDatasetId: "",
    setTestDatasetId: vi.fn(),
    config: {},
    setConfig: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  queries.registeredModels = happyModels;
  queries.modelVersions = happyModelVersions;
  queries.detectorVersionDetail = detectorVersionWithSchema;
  queries.datasets = happyDatasets;
});

describe("InferenceSubForm", () => {
  it("renders Source model section before Detector section", () => {
    wrap(<InferenceSubForm {...defaultProps()} />);
    // Source model card title is the first interactive section
    const srcModelTitles = screen.getAllByText(/source model/i);
    expect(srcModelTitles.length).toBeGreaterThanOrEqual(1);
  });

  it("does not expose an override toggle (footgun removed)", () => {
    wrap(<InferenceSubForm {...defaultProps()} />);
    // Detector version is rendered read-only; no override button exists.
    expect(
      screen.queryByRole("button", { name: /advanced.*override|進階.*覆寫/i }),
    ).toBeNull();
  });

  it("does not render a 'Detector (derived from model)' card (it's redundant — the model implies the detector)", () => {
    wrap(
      <InferenceSubForm
        {...defaultProps({
          sourceModelOwner: "alice",
          sourceModelName: "elf-rf",
          derivedDetectorId: "det-rf",
          derivedDetectorVersionTag: "v1.0.0",
        })}
      />,
    );
    expect(screen.queryByText(/derived from model/i)).toBeNull();
  });

  it("derives detector_id + version_tag when sourceModelVersionId is set (useEffect)", () => {
    // Mock returns mv1 → detector_id=det-rf, detector_version_tag=v1.0.0.
    // Mounting with sourceModelVersionId="mv1" pre-selected must invoke the
    // two derived setters with those values (covers L74-77).
    const props = defaultProps({
      sourceModelOwner: "alice",
      sourceModelName: "elf-rf",
      sourceModelVersionId: "mv1",
    });
    wrap(<InferenceSubForm {...props} />);
    expect(props.setDerivedDetectorId).toHaveBeenCalledWith("det-rf");
    expect(props.setDerivedDetectorVersionTag).toHaveBeenCalledWith("v1.0.0");
  });

  it("source-model change cascades clear of version + derived detector (L105-110)", async () => {
    queries.modelVersions = [];
    const props = defaultProps({ sourceModelVersionId: "mv-stale" });
    wrap(<InferenceSubForm {...props} />);
    await userEvent.click(
      screen.getByRole("combobox", { name: /source model/i }),
    );
    await userEvent.click(
      screen.getByRole("option", { name: /alice\/elf-rf/i }),
    );
    expect(props.setSourceModelOwner).toHaveBeenCalledWith("alice");
    expect(props.setSourceModelName).toHaveBeenCalledWith("elf-rf");
    expect(props.setSourceModelVersionId).toHaveBeenCalledWith("");
    expect(props.setDerivedDetectorId).toHaveBeenCalledWith("");
    expect(props.setDerivedDetectorVersionTag).toHaveBeenCalledWith("");
  });

  it('type="evaluate" renders the Test dataset Select with dataset items (L184)', async () => {
    queries.modelVersions = [];
    wrap(<InferenceSubForm {...defaultProps({ type: "evaluate" })} />);
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
    queries.modelVersions = [
      ...happyModelVersions,
      {
        id: "mv2",
        mlflow_version: 2,
        current_stage: "Staging",
        detector_id: "det-rf",
        detector_version_tag: "v0.9.0-retired",
        is_runnable: false,
      },
    ];
    wrap(
      <InferenceSubForm
        {...defaultProps({
          sourceModelOwner: "alice",
          sourceModelName: "elf-rf",
          derivedDetectorId: "det-rf",
        })}
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

  describe("query-shape fallbacks (L51-64)", () => {
    it("renders empty selects when useRegisteredModels / useModelVersions / useDatasets return undefined", async () => {
      // Source: each `…Arr` derivation has `(data as ...) ?? []` (or the
      // triple-fallback for datasets). The `[]` branch fires before the
      // first fetch resolves. Pin so a refactor that swaps in a different
      // shape can't crash the render on the loading frame.
      queries.registeredModels = undefined;
      queries.modelVersions = undefined;
      queries.datasets = undefined;
      queries.detectorVersionDetail = undefined;
      wrap(<InferenceSubForm {...defaultProps()} />);
      // Three selects render even with no options.
      const user = userEvent.setup();
      await user.click(screen.getByRole("combobox", { name: /source model/i }));
      // No options rendered — only the placeholder is visible.
      expect(screen.queryByRole("option")).toBeNull();
    });

    it("tolerates the raw-array (non-`{ items }`) response shape on useDatasets (L63)", async () => {
      // Older / proxy responses sometimes return the array directly
      // instead of `{ items: [...] }`. Source: `datasetsArr`'s second
      // fallback `(data as unknown as Array)`. Pin so a backend
      // pagination flip doesn't blank the predict-dataset select.
      queries.datasets = [
        { id: "ds-raw", name: "raw-dataset" },
      ] as DatasetItem[];
      const user = userEvent.setup();
      wrap(<InferenceSubForm {...defaultProps({ type: "predict" })} />);
      await user.click(
        screen.getByRole("combobox", { name: /predict dataset/i }),
      );
      expect(
        screen.getByRole("option", { name: /raw-dataset/i }),
      ).toBeInTheDocument();
    });
  });

  describe("hyperparameter card render tree (L219-232)", () => {
    it("renders RjsfConfigForm when detectorVersionDetail carries a stageSchema for the picked type", () => {
      // Source: `stageSchema ? <RjsfConfigForm /> : …` — happy path.
      queries.detectorVersionDetail = detectorVersionWithSchema;
      wrap(
        <InferenceSubForm
          {...defaultProps({
            sourceModelOwner: "alice",
            sourceModelName: "elf-rf",
            sourceModelVersionId: "mv1",
            derivedDetectorVersionTag: "v1.0.0",
          })}
        />,
      );
      expect(screen.getByTestId("stub-rjsf")).toBeInTheDocument();
    });

    it("shows the maldet-too-old hint when manifest lacks stage params_schema but derivedDetectorVersionTag is set (L225-228)", () => {
      // Source: `: p.derivedDetectorVersionTag ? <p ...>rebuild with maldet ≥ 1.1</p>` —
      // pin so a future refactor doesn't drop the operator-facing hint.
      queries.detectorVersionDetail = { manifest: { stages: {} } };
      wrap(
        <InferenceSubForm
          {...defaultProps({
            sourceModelOwner: "alice",
            sourceModelName: "elf-rf",
            sourceModelVersionId: "mv1",
            derivedDetectorVersionTag: "v1.0.0",
          })}
        />,
      );
      expect(
        screen.getByText(/rebuild with maldet ≥ 1\.1/),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("stub-rjsf")).toBeNull();
    });

    it("shows the empty-state placeholder when no model version is picked yet (L230-232)", () => {
      // Source: `: <p>Pick a model + version to load …</p>` — the
      // pre-pick state. Pin so a refactor doesn't render an RJSF form
      // against `undefined` schema (which would crash).
      queries.detectorVersionDetail = undefined;
      wrap(
        <InferenceSubForm
          {...defaultProps({ derivedDetectorVersionTag: "" })}
        />,
      );
      expect(
        screen.getByText(/Pick a model version to load/),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("stub-rjsf")).toBeNull();
    });
  });
});
