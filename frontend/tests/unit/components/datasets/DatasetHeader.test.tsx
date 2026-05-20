import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { Dataset } from "@/api/queries/datasets";

const { deleteMock, navigateMock, mutationState } = vi.hoisted(() => ({
  deleteMock: vi.fn(),
  navigateMock: vi.fn(),
  mutationState: { isPending: false },
}));

vi.mock("@/api/queries/datasets", async () => {
  const actual = await vi.importActual<object>("@/api/queries/datasets");
  return {
    ...actual,
    useDeleteDataset: () => ({
      mutateAsync: deleteMock,
      isPending: mutationState.isPending,
    }),
  };
});

vi.mock("react-router", () => ({
  useNavigate: () => navigateMock,
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

import { DatasetHeader } from "@/components/datasets/DatasetHeader";

const baseDataset = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "malware-train-2026-q1",
  description: "Quarterly malware corpus",
  visibility: "public",
} as unknown as Dataset;

beforeEach(() => {
  deleteMock.mockReset();
  deleteMock.mockResolvedValue(undefined);
  navigateMock.mockReset();
  mutationState.isPending = false;
});

describe("<DatasetHeader>", () => {
  it("renders the dataset name and visibility badge", () => {
    render(<DatasetHeader dataset={baseDataset} />);
    expect(
      screen.getByRole("heading", { name: "malware-train-2026-q1" }),
    ).toBeInTheDocument();
    expect(screen.getByText("public")).toBeInTheDocument();
  });

  it("falls back to an em-dash when description is null", () => {
    const ds = { ...baseDataset, description: null } as unknown as Dataset;
    render(<DatasetHeader dataset={ds} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders the description when present", () => {
    render(<DatasetHeader dataset={baseDataset} />);
    expect(screen.getByText("Quarterly malware corpus")).toBeInTheDocument();
  });

  it("links the Download CSV button to the dataset CSV endpoint", () => {
    render(<DatasetHeader dataset={baseDataset} />);
    const link = screen.getByRole("link", {
      name: /datasets\.detail\.downloadCsv/,
    });
    expect(link).toHaveAttribute(
      "href",
      `/api/v1/datasets/${baseDataset.id}/csv`,
    );
    expect(link).toHaveAttribute("download", `${baseDataset.name}.csv`);
  });

  it("disables the Delete button while the mutation is pending", () => {
    mutationState.isPending = true;
    render(<DatasetHeader dataset={baseDataset} />);
    expect(
      screen.getByRole("button", { name: /common\.delete/ }),
    ).toBeDisabled();
  });

  it("cancels the delete when the confirm prompt is dismissed", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<DatasetHeader dataset={baseDataset} />);
    await user.click(screen.getByRole("button", { name: /common\.delete/ }));
    expect(confirmSpy).toHaveBeenCalledOnce();
    expect(deleteMock).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("fires the delete mutation and navigates to /datasets on confirm", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<DatasetHeader dataset={baseDataset} />);
    await user.click(screen.getByRole("button", { name: /common\.delete/ }));
    expect(deleteMock).toHaveBeenCalledWith(baseDataset.id);
    expect(navigateMock).toHaveBeenCalledWith("/datasets");
    confirmSpy.mockRestore();
  });
});
