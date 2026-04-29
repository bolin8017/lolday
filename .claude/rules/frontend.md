---
paths:
  - "frontend/**/*.{ts,tsx,js,jsx,css,json}"
---

# Frontend rules (Vite + React + TS)

## Stack

- Vite 5, React 18, TypeScript 5.5, Tailwind 3.4, shadcn/ui (Radix primitives).
- Routing: react-router 7, file-based routing under `src/routes/`.
- Data: TanStack Query v5 + openapi-fetch + openapi-typescript (generates `src/api/schema.gen.ts`).
- Forms: react-hook-form + zod + `@hookform/resolvers`. JSON-Schema forms via `@rjsf/core` + `@rjsf/utils` + `@rjsf/validator-ajv8`.
- i18n: i18next + react-i18next + `i18next-browser-languagedetector`. zh-TW first-class; en is a translation. Do not introduce zh-CN.
- Tables: `@tanstack/react-table`.
- Charts: `recharts`.
- Misc: `date-fns`, `lucide-react` (icons), `class-variance-authority`, `clsx`, `tailwind-merge`, `cmdk`, `vaul`.

## File-based routing rules

- Files under `src/routes/`. Filename convention encodes routing.
- `_authed.*` prefix → requires login; layout is `_authed.tsx`.
- `$param` segment → path parameter (e.g. `_authed.jobs.$id.tsx`).
- `_index` → index route at that level.

## API client convention

- All API calls go through `src/api/client.ts` (openapi-fetch).
- Types come from `src/api/schema.gen.ts`. Regenerate via `pnpm gen-api-types` (calls `frontend/scripts/gen-api-types.sh`, which hits the backend OpenAPI doc).
- Do not hand-write `fetch`, `axios`, or SWR. Do not add a second HTTP client.

## State convention

- Server state → TanStack Query. Cache key conventions live in `src/api/queries/`.
- URL state → react-router (search params, path params, navigate).
- Form state → react-hook-form.
- Global client state is intentionally minimal. Do not introduce Redux / Zustand / Jotai without a written justification — most cases are server state in disguise.

## Component library discipline

- shadcn/ui is the first choice. Check `src/components/ui/` before adding a new primitive.
- Do not introduce Ant Design / Naive UI / ElementUI / Arco / TDesign (China-origin; see root `CLAUDE.md` hard rule).
- Do not introduce a competing component library. If something is missing, add a shadcn/ui component (`pnpm dlx shadcn@latest add <component>`).

## nginx CSP is strict (`script-src 'self'`)

The production frontend is served by `nginxinc/nginx-unprivileged` with CSP `default-src 'self'; script-src 'self'`. Any inline `<script>` is blocked at runtime. This includes:

- `dangerouslySetInnerHTML` for executable content.
- Build-time inlined scripts that some libraries inject.

If something works in `pnpm dev` but breaks in the built container, suspect CSP first. Test against the built image, not just the dev server.

## Format 紀律

Tooling: **Prettier** owns formatting; **ESLint** owns lint. They do not overlap (`eslint-config-prettier/flat` is appended to `eslint.config.js` to disable formatting rules in ESLint).

Config: `.prettierrc.json` and `.prettierignore` at repo root.

Manual commands from `frontend/`:

```bash
pnpm format          # write
pnpm format:check    # check (exits 1 if dirty)
pnpm lint            # ESLint
pnpm typecheck       # tsc --noEmit
```

### Forbidden additions

- `stylelint`, `husky`, `lint-staged`, `commitlint`, `prettier-eslint` — unnecessary integration layers.

### Rules

- Do not re-enable formatting rules in ESLint (Prettier owns formatting; doing so creates a fight between the two).
- Do not change `proseWrap` from `"preserve"` — Markdown paragraphs should not be auto-wrapped.
- The CSP `'self'` hard rule is unchanged.

## Tests

- `pnpm test` — vitest, unit + component (`frontend/tests/unit/`).
- `pnpm playwright test` — E2E (`frontend/tests/e2e/`). Some tests require the backend running.
- Run `pnpm typecheck && pnpm lint` before commit.

## Stray config build emits (gitignored)

`frontend/{playwright,vite,vitest,tailwind}.config.ts` are the source of truth and the only versions tracked in git. `tsc --build` over the configs occasionally produces `.js` and `.d.ts` siblings as accidental local output; these and `*.tsbuildinfo` are listed in the root `.gitignore` so they cannot be committed by accident.

Edit the `.ts` only. If you see a stray `.js` or `.d.ts` next to a config, delete it locally — runtime tools (vite/vitest/playwright/tailwind) read the `.ts` directly via tsx/esbuild.
