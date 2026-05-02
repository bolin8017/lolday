import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { ArtifactTree } from "@/components/common/ArtifactTree";

vi.mock("@/api/client", () => ({
  client: {
    GET: vi.fn().mockResolvedValue({
      data: {
        files: [{ path: "predictions.csv", is_dir: false, file_size: 100 }],
      },
      error: null,
    }),
  },
}));

describe("ArtifactTree download attribute", () => {
  it("sets download={name} so browser saves with the artifact basename", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ArtifactTree runId="r1" />
      </QueryClientProvider>,
    );
    const a = await waitFor(() =>
      screen.getByRole("link", { name: /download/i }),
    );
    expect(a.getAttribute("download")).toBe("predictions.csv");
  });
});
