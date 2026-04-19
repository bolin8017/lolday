import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema.gen";
import { parseError } from "./errors";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";

let on401Handler: (() => void) | null = null;

/** Called by App.tsx to wire redirect-to-login on 401. */
export function setOn401(handler: () => void) {
  on401Handler = handler;
}

const errorMiddleware: Middleware = {
  async onResponse({ response }) {
    if (response.ok) return undefined;
    const contentType = response.headers.get("content-type") ?? "";
    const body = contentType.includes("application/json")
      ? await response.clone().json().catch(() => null)
      : null;

    if (response.status === 401 && on401Handler) {
      on401Handler();
    }

    throw parseError(response.status, body);
  },
};

export const client = createClient<paths>({
  baseUrl: API_BASE,
  credentials: "include",   // send cookies on every request
});

client.use(errorMiddleware);
