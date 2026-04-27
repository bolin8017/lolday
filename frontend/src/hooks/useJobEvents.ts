import { useEffect, useState } from "react";

export type MaldetEvent = {
  ts: string;
  kind: string;
  [k: string]: unknown;
};

type HttpEvent = {
  id: string;
  ts: string;
  kind: string;
  payload: Record<string, unknown>;
};

type EventsPage = {
  events: HttpEvent[];
  next_since: string | null;
  next_id: string | null;
};

const PAGE_LIMIT = 500;

function flatten(e: HttpEvent): MaldetEvent {
  return { ts: e.ts, kind: e.kind, ...e.payload };
}

export function useJobEvents(
  jobId: string | null,
  isLive: boolean,
): MaldetEvent[] {
  const [events, setEvents] = useState<MaldetEvent[]>([]);

  useEffect(() => {
    if (!jobId) {
      setEvents([]);
      return;
    }
    let cancelled = false;
    let ws: WebSocket | null = null;

    (async () => {
      const all: MaldetEvent[] = [];
      let cursor: { since: string | null; since_id: string | null } = {
        since: null,
        since_id: null,
      };
      while (!cancelled) {
        const params = new URLSearchParams({ limit: String(PAGE_LIMIT) });
        if (cursor.since) params.set("since", cursor.since);
        if (cursor.since_id) params.set("since_id", cursor.since_id);
        const resp = await fetch(
          `/api/v1/jobs/${jobId}/events?${params.toString()}`,
          { credentials: "include" },
        );
        if (!resp.ok) break;
        const page = (await resp.json()) as EventsPage;
        for (const e of page.events) all.push(flatten(e));
        if (!page.next_since) break;
        cursor = { since: page.next_since, since_id: page.next_id };
      }
      if (cancelled) return;
      setEvents(all);

      if (!isLive) return;
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(
        `${scheme}://${window.location.host}/api/v1/jobs/${jobId}/events`,
      );
      ws.onmessage = (ev) => {
        try {
          const event = JSON.parse(ev.data) as MaldetEvent;
          setEvents((prev) => [...prev, event]);
        } catch {
          // Detector-side may emit occasional non-JSON lines (prints to
          // stdout before the JSONL writer flushes its first record).
          // Dropping these silently is intentional — the backend persists
          // valid events regardless, and the WS stream is best-effort.
        }
      };
    })();

    return () => {
      cancelled = true;
      ws?.close();
    };
  }, [jobId, isLive]);

  return events;
}
