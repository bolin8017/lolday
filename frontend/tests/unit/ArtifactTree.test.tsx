import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactTree } from "@/components/common/ArtifactTree";

const getMock = vi.hoisted(() => vi.fn());

vi.mock("@/api/client", () => ({
  client: { GET: getMock },
}));

function renderTree() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ArtifactTree runId="r1" />
    </QueryClientProvider>,
  );
}

describe("ArtifactTree", () => {
  beforeEach(() => {
    getMock.mockReset();
  });
  afterEach(() => {
    getMock.mockReset();
  });

  it("sets download={name} so browser saves with the artifact basename", async () => {
    getMock.mockResolvedValue({
      data: {
        files: [{ path: "predictions.csv", is_dir: false, file_size: 100 }],
      },
      error: null,
    });
    renderTree();
    const a = await waitFor(() =>
      screen.getByRole("link", { name: /download/i }),
    );
    expect(a.getAttribute("download")).toBe("predictions.csv");
  });

  it("renders the (empty) placeholder when the artifact list is empty", async () => {
    getMock.mockResolvedValue({ data: { files: [] }, error: null });
    renderTree();
    await waitFor(() => expect(screen.getByText("(empty)")).toBeTruthy());
  });

  it("expands a directory on click and collapses it on a second click", async () => {
    getMock.mockImplementation(async (_url, opts) => {
      const path = (opts?.params?.query as { path?: string } | undefined)?.path;
      if (!path) {
        return {
          data: {
            files: [{ path: "checkpoints", is_dir: true, file_size: 0 }],
          },
          error: null,
        };
      }
      return {
        data: {
          files: [
            { path: "checkpoints/epoch_5.ckpt", is_dir: false, file_size: 42 },
          ],
        },
        error: null,
      };
    });

    renderTree();
    const folderButton = await waitFor(() =>
      screen.getByRole("button", { name: /checkpoints/i }),
    );

    const user = userEvent.setup();
    await user.click(folderButton);
    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: /download/i }).getAttribute("href"),
      ).toContain("checkpoints%2Fepoch_5.ckpt"),
    );

    await user.click(folderButton);
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /download/i })).toBeNull(),
    );
  });
});
