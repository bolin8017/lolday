import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Three sibling route shells (`/datasets/new`, `/detectors/new`, `/jobs/new`)
// are pure wrappers around their respective sub-form component: heading +
// optional banner + the form. Each sub-form has its own dedicated unit test
// suite; stub them so this file exercises only the route shell's three
// invariants — heading text, breadcrumb handle, and sub-form mount.

vi.mock("@/components/forms/DatasetUploadForm", () => ({
  DatasetUploadForm: () => <form data-testid="stub-dataset-upload-form" />,
}));
vi.mock("@/components/forms/RegisterDetectorForm", () => ({
  RegisterDetectorForm: () => (
    <form data-testid="stub-register-detector-form" />
  ),
}));
vi.mock("@/components/forms/JobSubmitForm", () => ({
  JobSubmitForm: () => <form data-testid="stub-job-submit-form" />,
}));
vi.mock("@/components/common/GpuStatusBanner", () => ({
  GpuStatusBanner: () => <aside data-testid="stub-gpu-status-banner" />,
}));

import NewDatasetPage, {
  handle as datasetHandle,
} from "@/routes/_authed.datasets.new";
import NewDetectorPage, {
  handle as detectorHandle,
} from "@/routes/_authed.detectors.new";
import NewJobPage, { handle as jobHandle } from "@/routes/_authed.jobs.new";

describe("_authed.datasets.new.tsx (NewDatasetPage)", () => {
  it("renders the heading and mounts DatasetUploadForm", () => {
    const { getByText, getByTestId } = render(<NewDatasetPage />);
    expect(getByText(/Upload dataset/)).toBeInTheDocument();
    expect(getByTestId("stub-dataset-upload-form")).toBeInTheDocument();
  });

  it("exports the 'New dataset' breadcrumb handle", () => {
    // The route's handle is read by the layout breadcrumb (see
    // useBreadcrumb). Pin so a rename of the page doesn't silently drop
    // the breadcrumb label.
    expect(datasetHandle).toEqual({ breadcrumb: "New dataset" });
  });
});

describe("_authed.detectors.new.tsx (NewDetectorPage)", () => {
  it("renders the heading and mounts RegisterDetectorForm", () => {
    const { getByText, getByTestId } = render(<NewDetectorPage />);
    expect(getByText(/Register detector/)).toBeInTheDocument();
    expect(getByTestId("stub-register-detector-form")).toBeInTheDocument();
  });

  it("exports the 'New detector' breadcrumb handle", () => {
    expect(detectorHandle).toEqual({ breadcrumb: "New detector" });
  });
});

describe("_authed.jobs.new.tsx (NewJobPage)", () => {
  it("renders the heading and mounts GpuStatusBanner + JobSubmitForm", () => {
    const { getByText, getByTestId } = render(<NewJobPage />);
    expect(getByText(/Submit job/)).toBeInTheDocument();
    // The jobs/new shell is the only one with a pre-form banner — the
    // GpuStatusBanner surfaces queue-depth + free-GPU before the user
    // fills out hparams. Pin the mount so a refactor doesn't drop it.
    expect(getByTestId("stub-gpu-status-banner")).toBeInTheDocument();
    expect(getByTestId("stub-job-submit-form")).toBeInTheDocument();
  });

  it("exports the 'New job' breadcrumb handle", () => {
    expect(jobHandle).toEqual({ breadcrumb: "New job" });
  });
});
