import { renderHook } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useBreadcrumb } from "@/hooks/useBreadcrumb";

/**
 * `useBreadcrumb` reads `handle.breadcrumb` from every react-router match in
 * the active route tree. Routes can supply either a literal string or a
 * function that receives the route's `data` (loader output) — the
 * dashboard header renders the resulting trail. The hook has three
 * behavioural branches worth pinning:
 *
 * 1. Match without a `handle` object (most layouts) → skipped silently.
 * 2. Match with a string `handle.breadcrumb` → emitted verbatim.
 * 3. Match with a function `handle.breadcrumb` → invoked with `m.data`.
 */

const useMatchesMock = vi.fn();

vi.mock("react-router", () => ({
  useMatches: () => useMatchesMock(),
}));

beforeEach(() => {
  useMatchesMock.mockReset();
});

describe("useBreadcrumb", () => {
  it("returns an empty array when no match supplies a breadcrumb", () => {
    useMatchesMock.mockReturnValue([
      { pathname: "/", handle: undefined, data: undefined },
      { pathname: "/jobs", handle: { title: "Jobs" }, data: undefined },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([]);
  });

  it("emits a literal string breadcrumb", () => {
    useMatchesMock.mockReturnValue([
      { pathname: "/", handle: undefined, data: undefined },
      {
        pathname: "/detectors",
        handle: { breadcrumb: "Detectors" },
        data: undefined,
      },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([
      { pathname: "/detectors", label: "Detectors" },
    ]);
  });

  it("invokes a function breadcrumb with the match's data", () => {
    // The detector-detail route uses this shape:
    //   handle: { breadcrumb: (data) => data.detector.name }
    useMatchesMock.mockReturnValue([
      {
        pathname: "/detectors/123",
        handle: {
          breadcrumb: (d: unknown) =>
            `Detector: ${(d as { name: string }).name}`,
        },
        data: { name: "elf-rf" },
      },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([
      { pathname: "/detectors/123", label: "Detector: elf-rf" },
    ]);
  });

  it("preserves order: outer route crumbs appear before nested ones", () => {
    useMatchesMock.mockReturnValue([
      { pathname: "/", handle: undefined, data: undefined },
      {
        pathname: "/detectors",
        handle: { breadcrumb: "Detectors" },
        data: undefined,
      },
      {
        pathname: "/detectors/123",
        handle: { breadcrumb: (d: unknown) => (d as { name: string }).name },
        data: { name: "elf-rf" },
      },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current.map((c) => c.label)).toEqual(["Detectors", "elf-rf"]);
    // Pathnames in the same order — UI relies on this for the breadcrumb anchors.
    expect(result.current.map((c) => c.pathname)).toEqual([
      "/detectors",
      "/detectors/123",
    ]);
  });

  it("skips matches whose handle is non-object (e.g. a bare string)", () => {
    // The TypeScript guard requires `typeof m.handle === 'object' && m.handle !== null`.
    // A future router change that stuffs a string into `handle` must not crash.
    useMatchesMock.mockReturnValue([
      { pathname: "/", handle: "not-an-object", data: undefined },
      {
        pathname: "/jobs",
        handle: { breadcrumb: "Jobs" },
        data: undefined,
      },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([{ pathname: "/jobs", label: "Jobs" }]);
  });

  it("skips matches whose handle is null", () => {
    useMatchesMock.mockReturnValue([
      { pathname: "/", handle: null, data: undefined },
      { pathname: "/jobs", handle: { breadcrumb: "Jobs" }, data: undefined },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([{ pathname: "/jobs", label: "Jobs" }]);
  });

  it("skips matches whose handle object lacks a `breadcrumb` key", () => {
    useMatchesMock.mockReturnValue([
      {
        pathname: "/jobs",
        handle: { title: "Jobs", icon: "list" },
        data: undefined,
      },
      {
        pathname: "/jobs/1",
        handle: { breadcrumb: "Job detail" },
        data: undefined,
      },
    ]);
    const { result } = renderHook(() => useBreadcrumb());
    expect(result.current).toEqual([
      { pathname: "/jobs/1", label: "Job detail" },
    ]);
  });
});
