import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GpuStatusBanner } from "@/components/common/GpuStatusBanner";
import type { GpuStatus } from "@/api/queries/cluster";

vi.mock("@/api/queries/cluster", async () => {
  const actual = await vi.importActual<typeof import("@/api/queries/cluster")>(
    "@/api/queries/cluster",
  );
  return {
    ...actual,
    useClusterGpuStatus: vi.fn(),
    useClusterQueueDepth: vi.fn(),
  };
});

import {
  useClusterGpuStatus,
  useClusterQueueDepth,
} from "@/api/queries/cluster";

function renderBanner() {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <GpuStatusBanner />
    </QueryClientProvider>,
  );
}

const mockGpu = (data: Partial<GpuStatus> | null) =>
  vi.mocked(useClusterGpuStatus).mockReturnValue({
    data: data as GpuStatus | undefined,
    isLoading: data === null,
    isError: false,
  } as ReturnType<typeof useClusterGpuStatus>);

const mockQueue = (depth: number) =>
  vi.mocked(useClusterQueueDepth).mockReturnValue({
    data: { depth },
    isLoading: false,
    isError: false,
  } as ReturnType<typeof useClusterQueueDepth>);

beforeEach(() => {
  vi.mocked(useClusterGpuStatus).mockReset();
  vi.mocked(useClusterQueueDepth).mockReset();
  mockQueue(0);
});

describe("GpuStatusBanner", () => {
  it("renders loading state", () => {
    mockGpu(null);
    renderBanner();
    expect(screen.getByText(/loading cluster status/i)).toBeInTheDocument();
  });

  it("renders 2/2 free with no jobs", () => {
    mockGpu({
      total: 2,
      free_count: 2,
      in_use_by_lolday: 0,
      in_use_by_external: 0,
      fail_safe_active: false,
      fail_safe_reason: null,
      per_gpu: [
        { gpu_id: 0, state: "free", util_percent: 0, vram_used_mb: 0 },
        { gpu_id: 1, state: "free", util_percent: 0, vram_used_mb: 0 },
      ],
    });
    renderBanner();
    expect(screen.getByText("2 of 2")).toBeInTheDocument();
    expect(screen.getByText(/GPUs free/i)).toBeInTheDocument();
  });

  it("renders 1 lolday running, 1 free", () => {
    mockGpu({
      total: 2,
      free_count: 1,
      in_use_by_lolday: 1,
      in_use_by_external: 0,
      fail_safe_active: false,
      fail_safe_reason: null,
      per_gpu: [
        { gpu_id: 0, state: "lolday", util_percent: 87.5, vram_used_mb: 9240 },
        { gpu_id: 1, state: "free", util_percent: 0, vram_used_mb: 0 },
      ],
    });
    renderBanner();
    expect(screen.getByText(/GPU 0/)).toBeInTheDocument();
    expect(screen.getByText(/lolday/i)).toBeInTheDocument();
    expect(screen.getByText(/87.5%/)).toBeInTheDocument();
  });

  it("flags external GPU activity and queue-paused state", () => {
    mockGpu({
      total: 2,
      free_count: 0,
      in_use_by_lolday: 1,
      in_use_by_external: 1,
      fail_safe_active: false,
      fail_safe_reason: null,
      per_gpu: [
        { gpu_id: 0, state: "lolday", util_percent: 87, vram_used_mb: 9000 },
        { gpu_id: 1, state: "external", util_percent: 54, vram_used_mb: 7200 },
      ],
    });
    renderBanner();
    expect(
      screen.getByText(/external GPU activity detected/i),
    ).toBeInTheDocument();
  });

  it("renders fail-safe state with reason", () => {
    mockGpu({
      total: 2,
      free_count: 0,
      in_use_by_lolday: 0,
      in_use_by_external: 0,
      fail_safe_active: true,
      fail_safe_reason: "Prometheus HTTP error: connection refused",
      per_gpu: [],
    });
    renderBanner();
    expect(screen.getByText(/GPU status unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/connection refused/i)).toBeInTheDocument();
  });

  it("renders error state when query errors", () => {
    vi.mocked(useClusterGpuStatus).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as ReturnType<typeof useClusterGpuStatus>);
    renderBanner();
    expect(screen.getByText(/cluster status unavailable/i)).toBeInTheDocument();
  });
});
