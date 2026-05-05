export const NON_TERMINAL_JOB_STATUSES = [
  "queued_backend", // Phase 6 — backend FIFO holding state before Volcano dispatch
  "pending",
  "preparing",
  "running",
] as const;
export const NON_TERMINAL_BUILD_STATUSES = [
  "pending",
  "building",
  "scanning",
] as const;

export type Tone = "success" | "destructive" | "info" | "muted" | "warning";

const TONE_MAP: Record<string, Tone> = {
  succeeded: "success",
  success: "success",
  failed: "destructive",
  timeout: "destructive",
  cancelled: "muted",
  running: "info",
  scanning: "info",
  building: "info",
  preparing: "info",
  pending: "muted",
  queued_backend: "warning", // Phase 6 — held in backend FIFO before Volcano dispatch
};

export function statusTone(status: string): Tone {
  return TONE_MAP[status] ?? "muted";
}

export function isTerminal(status: string): boolean {
  return (
    !(NON_TERMINAL_JOB_STATUSES as readonly string[]).includes(status) &&
    !(NON_TERMINAL_BUILD_STATUSES as readonly string[]).includes(status)
  );
}
