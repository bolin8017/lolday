import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { useJobEvents } from "@/hooks/useJobEvents";

type MockFetch = ReturnType<typeof vi.fn>;

interface MockWebSocketInstance {
  readyState: number;
  onmessage: ((ev: { data: string }) => void) | null;
  onerror: (() => void) | null;
  onclose: (() => void) | null;
  onopen: (() => void) | null;
  close: () => void;
  url: string;
}

let wsInstances: MockWebSocketInstance[] = [];

class MockWebSocket implements MockWebSocketInstance {
  readyState = 1;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  close = vi.fn();
  url: string;

  constructor(url: string) {
    this.url = url;
    wsInstances.push(this);
  }
}

beforeEach(() => {
  wsInstances = [];
  global.fetch = vi.fn();
  (globalThis as unknown as { WebSocket: typeof MockWebSocket }).WebSocket =
    MockWebSocket;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useJobEvents", () => {
  it("fetches historical events via HTTP and flattens nested payload", async () => {
    const fetchMock = global.fetch as MockFetch;
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        events: [
          {
            id: "e1",
            ts: "2025-04-27T00:00:00Z",
            kind: "metric",
            payload: { name: "train_loss", value: 0.5, step: 1 },
          },
          {
            id: "e2",
            ts: "2025-04-27T00:00:01Z",
            kind: "metric",
            payload: { name: "train_loss", value: 0.4, step: 2 },
          },
        ],
        next_since: null,
        next_id: null,
      }),
    });

    const { result } = renderHook(() => useJobEvents("job-1", false));

    await waitFor(() => expect(result.current.length).toBe(2));
    expect(result.current[0]).toMatchObject({
      kind: "metric",
      name: "train_loss",
      value: 0.5,
      step: 1,
    });
    expect(result.current[1]).toMatchObject({
      kind: "metric",
      name: "train_loss",
      value: 0.4,
      step: 2,
    });
  });

  it("paginates through multiple pages using next_since cursor", async () => {
    const fetchMock = global.fetch as MockFetch;
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          events: [
            {
              id: "e1",
              ts: "2025-04-27T00:00:00Z",
              kind: "metric",
              payload: { name: "train_loss", value: 0.5, step: 1 },
            },
          ],
          next_since: "2025-04-27T00:00:00Z",
          next_id: "e1",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          events: [
            {
              id: "e2",
              ts: "2025-04-27T00:00:01Z",
              kind: "metric",
              payload: { name: "train_loss", value: 0.4, step: 2 },
            },
          ],
          next_since: null,
          next_id: null,
        }),
      });

    const { result } = renderHook(() => useJobEvents("job-1", false));

    await waitFor(() => expect(result.current.length).toBe(2));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const secondCallUrl = String(fetchMock.mock.calls[1][0]);
    expect(secondCallUrl).toContain("since=2025-04-27T00%3A00%3A00Z");
    expect(secondCallUrl).toContain("since_id=e1");
  });

  it("does not open a WebSocket when isLive is false", async () => {
    const fetchMock = global.fetch as MockFetch;
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ events: [], next_since: null, next_id: null }),
    });

    const { result } = renderHook(() => useJobEvents("job-1", false));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    expect(wsInstances.length).toBe(0);
    expect(result.current).toEqual([]);
  });

  it("opens a WebSocket and appends streamed events when isLive is true", async () => {
    const fetchMock = global.fetch as MockFetch;
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        events: [
          {
            id: "e1",
            ts: "2025-04-27T00:00:00Z",
            kind: "metric",
            payload: { name: "train_loss", value: 0.5, step: 1 },
          },
        ],
        next_since: null,
        next_id: null,
      }),
    });

    const { result } = renderHook(() => useJobEvents("job-1", true));

    await waitFor(() => expect(wsInstances.length).toBe(1));
    expect(result.current.length).toBe(1);

    const ws = wsInstances[0];
    expect(ws.url).toContain("/api/v1/jobs/job-1/events");
    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({
          ts: "2025-04-27T00:00:02Z",
          kind: "metric",
          name: "train_loss",
          value: 0.3,
          step: 3,
        }),
      });
    });

    await waitFor(() => expect(result.current.length).toBe(2));
    expect(result.current[1]).toMatchObject({
      name: "train_loss",
      value: 0.3,
      step: 3,
    });
  });

  it("returns no events and skips fetch/WS when jobId is null", async () => {
    const fetchMock = global.fetch as MockFetch;
    const { result } = renderHook(() => useJobEvents(null, true));

    await new Promise((r) => setTimeout(r, 10));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(wsInstances.length).toBe(0);
    expect(result.current).toEqual([]);
  });
});
