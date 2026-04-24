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
        // ignore malformed events
      }
    };

    return () => {
      ws.close();
    };
  }, [jobId]);

  return events;
}
