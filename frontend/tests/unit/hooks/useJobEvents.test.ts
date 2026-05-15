import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { useJobEvents } from "@/hooks/useJobEvents";

type MockFetch = ReturnType<typeof vi.fn>;

interface MockWebSocketInstance {
  readyState: number;
  onmessage: ((ev: { data: string; origin?: string }) => void) | null;
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
  // jsdom 29 marks window.WebSocket as read-only; defineProperty (rather than
  // direct assignment) is the mainstream workaround used by Testing Library
  // examples for stubbing built-in DOM constructors.
  Object.defineProperty(globalThis, "WebSocket", {
    configurable: true,
    writable: true,
    value: MockWebSocket,
  });
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

    await waitFor(() => expect(result.current.events.length).toBe(2));
    expect(result.current.error).toBeNull();
    expect(result.current.events[0]).toMatchObject({
      kind: "metric",
      name: "train_loss",
      value: 0.5,
      step: 1,
    });
    expect(result.current.events[1]).toMatchObject({
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

    await waitFor(() => expect(result.current.events.length).toBe(2));
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
    expect(result.current.events).toEqual([]);
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
    expect(result.current.events.length).toBe(1);

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

    await waitFor(() => expect(result.current.events.length).toBe(2));
    expect(result.current.events[1]).toMatchObject({
      name: "train_loss",
      value: 0.3,
      step: 3,
    });
  });

  it("returns empty events + null error and skips fetch/WS when jobId is null", async () => {
    const fetchMock = global.fetch as MockFetch;
    const { result } = renderHook(() => useJobEvents(null, true));

    await new Promise((r) => setTimeout(r, 10));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(wsInstances.length).toBe(0);
    expect(result.current.events).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it("surfaces an error on HTTP 4xx and does not open a WebSocket", async () => {
    const fetchMock = global.fetch as MockFetch;
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 403,
      json: async () => ({ detail: "forbidden" }),
    });

    const { result } = renderHook(() => useJobEvents("job-1", true));
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toContain("403");
    expect(result.current.events).toEqual([]);
    expect(wsInstances.length).toBe(0);
  });

  it("surfaces a network error from fetch", async () => {
    const fetchMock = global.fetch as MockFetch;
    fetchMock.mockRejectedValueOnce(new Error("ECONNREFUSED"));

    const { result } = renderHook(() => useJobEvents("job-1", false));
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toContain("ECONNREFUSED");
  });
});

describe("useJobEvents -- L-ws-origin-check", () => {
  it("drops messages whose origin does not match window.location.origin", async () => {
    const fetchMock = global.fetch as MockFetch;
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ events: [], next_since: null, next_id: null }),
    });

    const { result } = renderHook(() => useJobEvents("job-id-123", true));

    // Wait for the WebSocket to be constructed after the historical fetch.
    await waitFor(() => expect(wsInstances.length).toBe(1));
    const ws = wsInstances[0];

    // Fire a message with a foreign origin -- handler must drop it.
    act(() => {
      ws.onmessage?.({
        origin: "https://evil.example",
        data: JSON.stringify({ kind: "test", ts: "2026-05-14T00:00:00Z" }),
      });
    });

    // No event landed in state.
    expect(result.current.events).toEqual([]);
  });

  it("processes messages whose origin matches window.location.origin", async () => {
    const fetchMock = global.fetch as MockFetch;
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ events: [], next_since: null, next_id: null }),
    });

    const { result } = renderHook(() => useJobEvents("job-id-456", true));

    await waitFor(() => expect(wsInstances.length).toBe(1));
    const ws = wsInstances[0];

    act(() => {
      ws.onmessage?.({
        origin: window.location.origin,
        data: JSON.stringify({ kind: "test", ts: "2026-05-14T00:00:00Z" }),
      });
    });

    await waitFor(() => {
      expect(result.current.events).toHaveLength(1);
      expect(result.current.events[0].kind).toBe("test");
    });
  });
});
