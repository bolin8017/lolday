# lolday-frontend

React + Vite + shadcn/ui SPA for lolday.

## Dev

```bash
pnpm install
pnpm dev
```

Requires a reachable backend on `http://localhost:8000`. Auth: run the backend
with `AUTH_DEV_MODE=true` + `AUTH_DEV_EMAIL=<admin@your-domain>` so
`cf_access_user` returns a synthetic admin user without a real Cloudflare JWT.
Production rejects `AUTH_DEV_MODE=true` at boot (intentional — see
`.claude/rules/backend.md` §Startup fail-fast).

## Tests

```bash
pnpm typecheck         # tsc --noEmit
pnpm test              # vitest unit + component
pnpm playwright test   # E2E; requires backend up with AUTH_DEV_MODE=true
```

E2E specs see the dev user via the bypass. Multi-persona coverage uses the
`X-Dev-Persona` header (`admin` / `developer` / `user`) — the
`loginAs(page, role)` helper in `frontend/tests/e2e/helpers/auth.ts` sets
it via `page.context().setExtraHTTPHeaders`, so subsequent navigation +
fetch calls carry the persona. The dev-seed fixture surface (`POST
/api/v1/dev/seed-fixtures`, gated on `AUTH_DEV_MODE`) is called once from
Playwright's `globalSetup` before any worker spawns so every spec sees a
deterministic detector / version / dataset / queued-job / registered-model /
model-version set. The legacy `E2E_ADMIN_EMAIL` / `E2E_ADMIN_PASSWORD` env
vars are vestigial — the password-based auth flow was retired in Phase 10.2
when Cloudflare Access took over.

## E2E against a deployed stack

Deployed stacks use real CF Access SSO, which the suite currently has no helper
for. The dev-seed endpoint is also unreachable against a deployed stack
(`AUTH_DEV_MODE=true` is rejected in production by `Settings.validate_sso_config`).
Most specs will therefore `test.skip(...)` against a deployed backend until
a production-safe test-seeding helper lands.

## Project context

- Project overview, stack, quick-start: [../README.md](../README.md)
- Architecture and data flows: [../docs/architecture.md](../docs/architecture.md)
- Frontend rules (Vite / shadcn / CSP / TanStack / RJSF): [../.claude/rules/frontend.md](../.claude/rules/frontend.md)
