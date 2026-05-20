import { render } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";

type DatasetDetail = {
  id: string;
  name: string;
  label_distribution?: Record<string, number> | null;
  family_distribution?: Record<string, number> | null;
};

const { queryState, capturedProps } = vi.hoisted(() => ({
  queryState: {
    data: undefined as DatasetDetail | undefined,
  },
  capturedProps: {
    labelDist: undefined as Record<string, number> | undefined,
    familyDist: undefined as Record<string, number> | undefined,
  },
}));

vi.mock("@/api/queries/datasets", () => ({
  useDataset: () => ({ data: queryState.data }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// Heavy child components each have their own unit suites.
// Stub them so this test isolates only the route shell's loading
// branch, chart-data shaping, and conditional family-card rendering.
vi.mock("@/components/datasets/DatasetHeader", () => ({
  DatasetHeader: () => <div data-testid="stub-dataset-header" />,
}));
vi.mock("@/components/datasets/DatasetKpiStrip", () => ({
  DatasetKpiStrip: () => <div data-testid="stub-dataset-kpi" />,
}));
vi.mock("@/components/datasets/DatasetMetadataDetails", () => ({
  DatasetMetadataDetails: () => <div data-testid="stub-dataset-meta" />,
}));
vi.mock("@/components/charts/LabelDistribution", () => ({
  LabelDistribution: ({ data }: { data: Record<string, number> }) => {
    capturedProps.labelDist = data;
    return <div data-testid="stub-label-dist" />;
  },
}));
vi.mock("@/components/charts/FamilyDistribution", () => ({
  FamilyDistribution: ({ data }: { data: Record<string, number> }) => {
    capturedProps.familyDist = data;
    return <div data-testid="stub-family-dist" />;
  },
}));

import DatasetDetailPage, {
  handle as datasetDetailHandle,
} from "@/routes/_authed.datasets.$id";

function renderAt(id = "ds-1") {
  return render(
    <MemoryRouter initialEntries={[`/datasets/${id}`]}>
      <Routes>
        <Route path="/datasets/:id" element={<DatasetDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  queryState.data = undefined;
  capturedProps.labelDist = undefined;
  capturedProps.familyDist = undefined;
});

describe("_authed.datasets.$id.tsx (DatasetDetailPage)", () => {
  it("renders Loading… while the query is in flight", () => {
    queryState.data = undefined;
    const { getByText, queryByTestId } = renderAt();
    expect(getByText(/Loading…/)).toBeInTheDocument();
    expect(queryByTestId("stub-dataset-header")).toBeNull();
    expect(queryByTestId("stub-label-dist")).toBeNull();
  });

  it("renders header / KPI / label-dist / metadata when data resolves", () => {
    queryState.data = {
      id: "ds-1",
      name: "First",
      label_distribution: { benign: 100, malware: 50 },
      family_distribution: null,
    };
    const { getByTestId } = renderAt();
    expect(getByTestId("stub-dataset-header")).toBeInTheDocument();
    expect(getByTestId("stub-dataset-kpi")).toBeInTheDocument();
    expect(getByTestId("stub-label-dist")).toBeInTheDocument();
    expect(getByTestId("stub-dataset-meta")).toBeInTheDocument();
  });

  it("does NOT render the family-distribution card when the dataset lacks one", () => {
    // The route gates the family card behind a truthy
    // `data.family_distribution`. Pin so a refactor doesn't render an
    // empty chart for datasets that never had family-level labels.
    queryState.data = {
      id: "ds-1",
      name: "First",
      label_distribution: { benign: 100 },
      family_distribution: null,
    };
    const { queryByTestId } = renderAt();
    expect(queryByTestId("stub-family-dist")).toBeNull();
  });

  it("renders the family-distribution card when the dataset has one", () => {
    queryState.data = {
      id: "ds-1",
      name: "First",
      label_distribution: { benign: 100, malware: 50 },
      family_distribution: { gafgyt: 30, mirai: 20 },
    };
    const { getByTestId } = renderAt();
    expect(getByTestId("stub-family-dist")).toBeInTheDocument();
    expect(capturedProps.familyDist).toEqual({ gafgyt: 30, mirai: 20 });
  });

  it("shapes a null label_distribution to {} before passing to the chart", () => {
    // Defensive guard `(data.label_distribution ?? {})` — pin so a
    // dataset stored without a label distribution doesn't crash the
    // chart (which would otherwise call Object.entries on null).
    queryState.data = {
      id: "ds-1",
      name: "First",
      label_distribution: null,
      family_distribution: null,
    };
    renderAt();
    expect(capturedProps.labelDist).toEqual({});
  });

  it("exports the 'Dataset' breadcrumb handle", () => {
    expect(datasetDetailHandle).toEqual({ breadcrumb: "Dataset" });
  });
});
