import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toast, useToast } from "@/hooks/use-toast";

// The toast queue is module-scoped (memoryState + listeners). To keep tests
// isolated we reset the module state between tests by dismissing all toasts
// and advancing fake timers past TOAST_REMOVE_DELAY (1_000_000 ms).
function resetToastState() {
  const { result, unmount } = renderHook(() => useToast());
  act(() => {
    result.current.dismiss();
  });
  act(() => {
    vi.advanceTimersByTime(1_000_000);
  });
  unmount();
}

describe("toast() module-scoped queue", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    resetToastState();
    vi.useRealTimers();
  });

  it("toast() appends a row with open=true and returns id+update+dismiss", () => {
    const { result, unmount } = renderHook(() => useToast());
    let handle: ReturnType<typeof toast>;
    act(() => {
      handle = toast({ title: "hello" });
    });
    expect(handle!.id).toMatch(/^\d+$/);
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].open).toBe(true);
    expect(result.current.toasts[0].title).toBe("hello");
    unmount();
  });

  it("TOAST_LIMIT=1 — a second toast() replaces the previous row", () => {
    const { result, unmount } = renderHook(() => useToast());
    act(() => {
      toast({ title: "first" });
      toast({ title: "second" });
    });
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].title).toBe("second");
    unmount();
  });

  it("handle.dismiss() sets the row's open=false (Radix unmount animation runs)", () => {
    const { result, unmount } = renderHook(() => useToast());
    let handle: ReturnType<typeof toast>;
    act(() => {
      handle = toast({ title: "to-dismiss" });
    });
    expect(result.current.toasts[0].open).toBe(true);
    act(() => {
      handle!.dismiss();
    });
    expect(result.current.toasts[0].open).toBe(false);
    unmount();
  });

  it("handle.update() merges new fields into the row", () => {
    const { result, unmount } = renderHook(() => useToast());
    let handle: ReturnType<typeof toast>;
    act(() => {
      handle = toast({ title: "original" });
    });
    act(() => {
      handle!.update({
        id: handle!.id,
        title: "updated",
      } as unknown as Parameters<typeof handle.update>[0]);
    });
    expect(result.current.toasts[0].title).toBe("updated");
    unmount();
  });

  it("useToast().dismiss() with no id closes every open toast", () => {
    const { result, unmount } = renderHook(() => useToast());
    act(() => {
      toast({ title: "only-one-survives-TOAST_LIMIT" });
    });
    expect(result.current.toasts[0].open).toBe(true);
    act(() => {
      result.current.dismiss();
    });
    expect(result.current.toasts.every((t) => t.open === false)).toBe(true);
    unmount();
  });

  it("REMOVE_TOAST timer purges the row after TOAST_REMOVE_DELAY", () => {
    const { result, unmount } = renderHook(() => useToast());
    let handle: ReturnType<typeof toast>;
    act(() => {
      handle = toast({ title: "purge" });
    });
    act(() => {
      handle!.dismiss();
    });
    expect(result.current.toasts).toHaveLength(1);
    // TOAST_REMOVE_DELAY = 1_000_000ms.
    act(() => {
      vi.advanceTimersByTime(1_000_000);
    });
    expect(result.current.toasts).toHaveLength(0);
    unmount();
  });

  it("onOpenChange(false) (Radix close button) calls the internal dismiss path", () => {
    const { result, unmount } = renderHook(() => useToast());
    act(() => {
      toast({ title: "click-X" });
    });
    const row = result.current.toasts[0];
    expect(row.open).toBe(true);
    act(() => {
      row.onOpenChange?.(false);
    });
    expect(result.current.toasts[0].open).toBe(false);
    unmount();
  });

  it("useToast subscribes once on mount, unsubscribes on unmount (no listener leak)", () => {
    const r1 = renderHook(() => useToast());
    const r2 = renderHook(() => useToast());
    act(() => {
      toast({ title: "broadcast" });
    });
    expect(r1.result.current.toasts).toHaveLength(1);
    expect(r2.result.current.toasts).toHaveLength(1);

    r1.unmount();
    act(() => {
      toast({ title: "after-unmount" });
    });
    expect(r2.result.current.toasts[0].title).toBe("after-unmount");
    r2.unmount();
  });
});
