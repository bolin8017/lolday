import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TrainSubForm } from "@/components/forms/TrainSubForm";

vi.mock("@/api/queries/detectors", () => ({
  useDetectors: () => ({
    data: { items: [{ id: "d1", display_name: "ELF RF" }] },
  }),
  useDetectorVersions: () => ({
    data: { items: [{ id: "v1", git_tag: "v1.0.0", status: "active" }] },
  }),
  useDetectorVersion: () => ({
    data: { manifest: { stages: { train: { params_schema: {} } } } },
  }),
}));
vi.mock("@/api/queries/datasets", () => ({
  useDatasets: () => ({
    data: { items: [{ id: "ds1", name: "malware-train" }] },
  }),
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("TrainSubForm", () => {
  it("renders Detector + Train dataset + Test dataset (optional) sections", () => {
    wrap(
      <TrainSubForm
        detectorId=""
        setDetectorId={() => {}}
        versionTag=""
        setVersionTag={() => {}}
        trainDatasetId=""
        setTrainDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    // CardTitle + Label both render "Detector" — use getAllByText
    expect(screen.getAllByText(/^detector$/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/^train dataset$/i)).toBeInTheDocument();
    expect(screen.getByText(/^test dataset$/i)).toBeInTheDocument();
  });

  it("picking a Detector resets the Version selection back to empty", async () => {
    const setDetectorId = vi.fn();
    const setVersionTag = vi.fn();
    const user = userEvent.setup();
    wrap(
      <TrainSubForm
        detectorId=""
        setDetectorId={setDetectorId}
        versionTag="v1.0.0"
        setVersionTag={setVersionTag}
        trainDatasetId=""
        setTrainDatasetId={() => {}}
        testDatasetId=""
        setTestDatasetId={() => {}}
        config={{}}
        setConfig={() => {}}
      />,
    );
    // Open the Detector combobox and pick the only item.
    await user.click(screen.getByRole("combobox", { name: /^detector$/i }));
    await user.click(screen.getByRole("option", { name: /ELF RF/ }));
    // The Detector onValueChange does TWO things: sets the new detector id
    // AND clears the version tag, so a stale version doesn't leak into a
    // freshly picked detector.
    expect(setDetectorId).toHaveBeenCalledWith("d1");
    expect(setVersionTag).toHaveBeenCalledWith("");
  });
});
