import { useEffect, useState } from "react";

export type MaldetEvent = {
  ts: string;
  kind: string;
  [k: string]: unknown;
};

export function useJobEvents(jobId: string | null): MaldetEvent[] {
  const [events, setEvents] = useState<MaldetEvent[]>([]);

  useEffect(() => {
    if (!jobId) return;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(
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

    return () => {
      ws.close();
    };
  }, [jobId]);

  return events;
}
