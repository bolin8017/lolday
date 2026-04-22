import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema.gen";
import { parseError } from "./errors";

// openapi-fetch prepends baseUrl to each operation path. The generated schema
// paths already include the `/api/v1` prefix, so baseUrl must be empty.
const API_BASE = "";

/**
 * 401 is not a user action — Cloudflare Access owns login. A 401 here means
 * the JWT header was missing/invalid at the edge (infra event). The
 * `_authed` layout renders a diagnostic page that routes the user back
 * through Cloudflare Access; this middleware just throws for non-2xx.
 */
const errorMiddleware: Middleware = {
  async onResponse({ response }) {
    if (response.ok) return undefined;
    const contentType = response.headers.get("content-type") ?? "";
    const body = contentType.includes("application/json")
      ? await response.clone().json().catch(() => null)
      : null;
    throw parseError(response.status, body);
  },
};

export const client = createClient<paths>({
  baseUrl: API_BASE,
});

client.use(errorMiddleware);
