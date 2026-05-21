import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

type DetectorItem = { id: string; display_name: string };
type VersionItem = { id: string; git_tag: string; status: string };
type DatasetItem = { id: string; name: string };

const { queries } = vi.hoisted(() => ({
  queries: {
    detectors: undefined as unknown,
    versions: undefined as unknown,
    versionDetail: undefined as unknown,
    datasets: undefined as unknown,
  },
}));

vi.mock("@/api/queries/detectors", () => ({
  useDetectors: () => ({ data: queries.detectors }),
  useDetectorVersions: () => ({ data: queries.versions }),
  useDetectorVersion: () => ({ data: queries.versionDetail }),
}));

vi.mock("@/api/queries/datasets", () => ({
  useDatasets: () => ({ data: queries.datasets }),
}));

// RjsfConfigForm has its own dedicated suite — stub so we exercise the
// stageSchema branch in TrainSubForm without re-running RJSF's render
// path on every TrainSubForm test.
vi.mock("@/components/forms/RjsfConfigForm", () => ({
  RjsfConfigForm: () => <div data-testid="stub-rjsf" />,
}));

import { TrainSubForm } from "@/components/forms/TrainSubForm";

function defaultProps(
  overrides: Partial<React.ComponentProps<typeof TrainSubForm>> = {},
) {
  return {
    detectorId: "",
    setDetectorId: vi.fn(),
    versionTag: "",
    setVersionTag: vi.fn(),
    trainDatasetId: "",
    setTrainDatasetId: vi.fn(),
    testDatasetId: "",
    setTestDatasetId: vi.fn(),
    config: {},
    setConfig: vi.fn(),
    ...overrides,
  };
}

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const happyDetectors: { items: DetectorItem[] } = {
  items: [{ id: "d1", display_name: "ELF RF" }],
};
const happyVersions: { items: VersionItem[] } = {
  items: [
    { id: "v1", git_tag: "v1.0.0", status: "active" },
    { id: "v2", git_tag: "v0.9.0", status: "deprecated" },
  ],
};
const happyDatasets: { items: DatasetItem[] } = {
  items: [{ id: "ds1", name: "malware-train" }],
};
const versionWithSchema = {
  manifest: { stages: { train: { params_schema: { type: "object" } } } },
};
const versionWithoutSchema = { manifest: { stages: {} } };

beforeEach(() => {
  queries.detectors = happyDetectors;
  queries.versions = happyVersions;
  queries.versionDetail = versionWithSchema;
  queries.datasets = happyDatasets;
});

describe("TrainSubForm", () => {
  it("renders Detector + Train dataset + Test dataset (optional) sections", () => {
    wrap(<TrainSubForm {...defaultProps()} />);
    // CardTitle + Label both render "Detector" — use getAllByText
    expect(screen.getAllByText(/^detector$/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/^train dataset$/i)).toBeInTheDocument();
    expect(screen.getByText(/^test dataset$/i)).toBeInTheDocument();
  });

  it("picking a Detector resets the Version selection back to empty", async () => {
    const props = defaultProps({ versionTag: "v1.0.0" });
    const user = userEvent.setup();
    wrap(<TrainSubForm {...props} />);
    // Open the Detector combobox and pick the only item.
    await user.click(screen.getByRole("combobox", { name: /^detector$/i }));
    await user.click(screen.getByRole("option", { name: /ELF RF/ }));
    // The Detector onValueChange does TWO things: sets the new detector id
    // AND clears the version tag, so a stale version doesn't leak into a
    // freshly picked detector.
    expect(props.setDetectorId).toHaveBeenCalledWith("d1");
    expect(props.setVersionTag).toHaveBeenCalledWith("");
  });

  describe("query-shape fallbacks (L44-58)", () => {
    it("renders empty selects when useDetectors / useDetectorVersions / useDatasets return undefined", () => {
      // Source: each `…Arr` derivation has a triple-fallback
      //   data.items ?? data (raw array) ?? []
      // The `[]` branch fires before the first fetch resolves. Pin so a
      // refactor that swaps in a different shape can't crash the render
      // on the loading frame.
      queries.detectors = undefined;
      queries.versions = undefined;
      queries.datasets = undefined;
      queries.versionDetail = undefined;
      wrap(<TrainSubForm {...defaultProps()} />);
      // The three selects render even though no options exist (each
      // SelectContent body is just `[].map(...)`).
      expect(
        screen.getByRole("combobox", { name: /^detector$/i }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("combobox", { name: /^train dataset$/i }),
      ).toBeInTheDocument();
      // Without a versionTag and without a stageSchema, the placeholder
      // message renders.
      expect(
        screen.getByText(/pick a detector \+ version/i),
      ).toBeInTheDocument();
    });

    it("tolerates the raw-array (non-`{ items }`) response shape on detectors / versions / datasets", async () => {
      // Older / proxy responses sometimes return the array directly
      // instead of `{ items: [...] }`. Source: each `…Arr` derivation's
      // second fallback `(data as unknown as Array)`. Pin so a backend
      // pagination flip doesn't blank the three selects.
      queries.detectors = [
        { id: "d-raw", display_name: "Raw Detector" },
      ] as DetectorItem[];
      queries.versions = [
        { id: "v-raw", git_tag: "v2.0.0", status: "active" },
      ] as VersionItem[];
      queries.datasets = [
        { id: "ds-raw", name: "raw-dataset" },
      ] as DatasetItem[];
      const user = userEvent.setup();
      wrap(<TrainSubForm {...defaultProps({ detectorId: "d-raw" })} />);
      // Detector option is present.
      await user.click(screen.getByRole("combobox", { name: /^detector$/i }));
      expect(
        screen.getByRole("option", { name: /Raw Detector/ }),
      ).toBeInTheDocument();
    });
  });

  describe("version filter (L104-110)", () => {
    it("only renders versions whose status === 'active' in the Version combobox", async () => {
      // Source: `versionsArr.filter((v) => v.status === "active")` — pin
      // the deprecated-status path so a backend that returns mixed
      // statuses (e.g. soft-deleted) doesn't accidentally surface the
      // dead versions in the picker.
      const user = userEvent.setup();
      wrap(<TrainSubForm {...defaultProps({ detectorId: "d1" })} />);
      await user.click(screen.getByRole("combobox", { name: /^version$/i }));
      expect(
        screen.getByRole("option", { name: /v1\.0\.0/ }),
      ).toBeInTheDocument();
      // The deprecated v0.9.0 entry exists in the mock list but must NOT
      // appear in the rendered options.
      expect(screen.queryByRole("option", { name: /v0\.9\.0/ })).toBeNull();
    });
  });

  describe("hyperparameter card render tree (L170-185)", () => {
    it("renders RjsfConfigForm when versionDetail carries a train.params_schema", () => {
      // Source: `stageSchema ? <RjsfConfigForm /> : ...` — happy path.
      queries.versionDetail = versionWithSchema;
      wrap(<TrainSubForm {...defaultProps({ versionTag: "v1.0.0" })} />);
      expect(screen.getByTestId("stub-rjsf")).toBeInTheDocument();
    });

    it("shows the maldet-too-old error when the picked version's manifest lacks a params_schema", () => {
      // Source: `: p.versionTag ? <p ...>rebuild with maldet ≥ 1.1</p>` —
      // the post-pre-maldet-1.1 footgun. Pin so a future refactor doesn't
      // silently drop the operator-facing hint.
      queries.versionDetail = versionWithoutSchema;
      wrap(<TrainSubForm {...defaultProps({ versionTag: "v1.0.0" })} />);
      expect(
        screen.getByText(/rebuild with maldet ≥ 1\.1/),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("stub-rjsf")).toBeNull();
    });

    it("shows the empty-state placeholder when no versionTag is selected yet", () => {
      // Source: `: <p>Pick a detector + version to load …</p>` — the
      // pre-pick state. Pin so a refactor doesn't render an RJSF form
      // against `undefined` schema (which would crash).
      queries.versionDetail = undefined;
      wrap(<TrainSubForm {...defaultProps({ versionTag: "" })} />);
      expect(
        screen.getByText(/Pick a detector \+ version to load/),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("stub-rjsf")).toBeNull();
    });
  });
});
