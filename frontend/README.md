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

E2E specs see the dev user via the bypass; the helper at
`frontend/tests/e2e/helpers.ts` documents the flow plus open TODOs around
multi-persona test fixtures (tracked in `docs/architecture.md` §10 #13). The
legacy `E2E_ADMIN_EMAIL` / `E2E_ADMIN_PASSWORD` env vars are vestigial — the
password-based auth flow was retired in Phase 10.2 when Cloudflare Access took
over.

## E2E against a deployed stack

Deployed stacks use real CF Access SSO, which the suite currently has no helper
for. Most specs will `test.skip(...)` against a deployed backend until a
test-seeding surface lands (tracked in `docs/architecture.md` §10 #12).

## Project context

- Project overview, stack, quick-start: [../README.md](../README.md)
- Architecture and data flows: [../docs/architecture.md](../docs/architecture.md)
- Frontend rules (Vite / shadcn / CSP / TanStack / RJSF): [../.claude/rules/frontend.md](../.claude/rules/frontend.md)
