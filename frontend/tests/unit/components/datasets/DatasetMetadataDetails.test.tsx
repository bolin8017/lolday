import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { Dataset } from "@/api/queries/datasets";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

import { DatasetMetadataDetails } from "@/components/datasets/DatasetMetadataDetails";

const dataset = {
  id: "ds-id",
  name: "ds",
  owner_id: "11111111-1111-1111-1111-111111111111",
  csv_checksum:
    "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
} as unknown as Dataset;

beforeEach(() => {
  vi.useRealTimers();
});

describe("<DatasetMetadataDetails>", () => {
  it("collapses metadata content by default and reveals it after toggling", async () => {
    const user = userEvent.setup();
    render(<DatasetMetadataDetails dataset={dataset} />);
    expect(screen.queryByText(dataset.owner_id)).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: /datasets\.detail\.metadata/ }),
    );
    expect(screen.getByText(dataset.owner_id)).toBeInTheDocument();
    expect(screen.getByText(dataset.csv_checksum)).toBeInTheDocument();
  });

  it("copies the checksum to the clipboard and flips the label for 2s", async () => {
    const user = userEvent.setup();
    const writeText = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue(undefined);
    render(<DatasetMetadataDetails dataset={dataset} />);
    await user.click(
      screen.getByRole("button", { name: /datasets\.detail\.metadata/ }),
    );
    await user.click(screen.getByRole("button", { name: /common\.copy/ }));
    expect(writeText).toHaveBeenCalledWith(dataset.csv_checksum);
    expect(
      await screen.findByRole("button", { name: /common\.copied/ }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole(
        "button",
        { name: /common\.copy/ },
        { timeout: 3_000 },
      ),
    ).toBeInTheDocument();
    writeText.mockRestore();
  });
});
