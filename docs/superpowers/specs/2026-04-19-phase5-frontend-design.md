# Phase 5: Frontend — Design Specification

## Overview

Phase 5 delivers the first-class web UI that wraps the backend built in Phases 2–4. Lab members log in with a browser, register detectors from Git, upload dataset configs, submit train / evaluate / predict jobs, watch them run live, download artifacts, and manage the model registry — all without touching `curl`, `kubectl`, or MLflow's port-forwarded UI.

**Goal:** A lab member visits `https://lolday.islab.local/`, logs in, and completes the full Phase 4 E2E flow (register detector → build → upload dataset → submit job → watch logs → download predictions → promote model) entirely through the browser.

**Constraints:**
- Must not break SSH on server30 (port 9453) — pure in-cluster work, no host-level changes beyond Traefik ingress wiring.
- No custom code where an open-source tool exists (lolday principle).
- Avoid China-origin component libraries and SaaS (Taiwan lab preference).
- Backend API surface is frozen — Phase 5 adds only one backend change (add cookie auth transport alongside existing Bearer). All other endpoints are consumed as-is.
- UI language is English-first (academic / international demo). `react-i18next` scaffold is in place so `zh-TW.json` can be filled later without refactor.
- Single server for now (server30, 2× RTX 2080 Ti); frontend is CPU-only and runs as a single-replica Deployment.

---

## Scope

Phase 5 covers main spec §6.1 (user submission flow), §6.5 (completion + results page), §7 (results + model registry UX), and §10.3 (user-facing job UI) as a cohesive delivery — the frontend for everything Phase 3 + Phase 4 exposed via API.

### In scope (7 core screens)

1. **Auth** — Login page, logout action, route guards. No self-registration, no forgot-password UI (API exists, wired later).
2. **Profile** — Change password, set / update / clear GitHub PAT (required for detector builds).
3. **Detectors** — List, detail (metadata + versions table + builds table), register new, trigger build from Git tag, cancel build, view build log tail + status.
4. **Datasets** — List, upload CSV (file picker + textarea paste, ≤10 MB), detail (metadata + stats + label distribution chart + sample-count badge), delete (soft).
5. **Jobs** — Submit (single page, fields reveal by `type`; detector + version → dataset(s) → optional source model → RJSF-rendered config form), list, detail (status + metrics card + live log tail + artifact tree + download), cancel.
6. **Runs** — List (per-experiment runs table), detail (params / metrics / tags / confusion matrix HTML heatmap / artifact tree + download). Self-built against backend's MLflow proxy.
7. **Models** — Registered model list, version list, stage transition (Staging ↔ Production ↔ Archived) with auto-archive confirmation.

### Out of scope (deferred — Phase 6 or later)

- **Admin UI** — user list, role editing, audit log viewing. Backend APIs exist; use `curl` for now.
- **Self-registration** — public `/register` page. Lab is invite-only; admin bootstraps via env + creates teammates via admin API.
- **Forgot-password** flow in the UI — API exists (`POST /api/v1/auth/forgot-password`), email delivery is Phase 6.
- **Cross-run comparison** — multi-run side-by-side. MLflow UI already does this; we will link out once Phase 6 exposes MLflow via Cloudflare Access.
- **GPU queue dashboard** — needs Phase 6 monitoring stack (DCGM + Prometheus + Grafana). Job detail page shows only its own pod's GPU allocation from backend metadata.
- **In-app notifications / toast feed** — global notification center with WebSocket delivery. Phase 5 uses toast for one-shot action results only; job completion notifications go through Phase 6 email.
- **Detector / dataset edit UI** — backend `PATCH /detectors/{id}`, `POST /datasets/{id}/clone` exist. Phase 5 ships read + create + delete only; edit / clone UI waits for real demand.
- **Multi-step job wizard / hyperparameter sweep UI** — single-page submit is sufficient for MVP; sweeps are a Future item.
- **Dark mode toggle** — shadcn/ui supports it trivially; ship dark-only theme aligned with sidebar aesthetic to save time choosing defaults.
- **Mobile-first responsive** — target ≥1280px desktop; tablet / phone layouts are best-effort, not tested.

---

## Architecture

```
Browser (user@lab)
  │  HTTPS (Phase 6: Cloudflare Access + Tunnel)
  ▼
┌──────────────────────────────────────────────────────────────┐
│ K3s Cluster — namespace: lolday                              │
│                                                              │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ Traefik Ingress  (host: lolday.islab.local)              │ │
│ │   /api/v1/*   → svc/backend:8000                         │ │
│ │   /*          → svc/frontend:80                          │ │
│ └──────┬────────────────────────────────┬──────────────────┘ │
│        │                                │                    │
│        ▼                                ▼                    │
│ ┌──────────────────────────┐   ┌────────────────────────┐    │
│ │ frontend Deployment      │   │ backend (existing)     │    │
│ │  nginx 1.27 + static SPA │   │  FastAPI + uvicorn     │    │
│ │  image:                  │   │  auth: +CookieTransport│    │
│ │   harbor.lolday.svc:80/  │   │        (existing Bearer│    │
│ │   lolday/lolday-frontend │   │         kept for curl) │    │
│ │   :phase5                │   │                        │    │
│ │  replicas: 1             │   │  routes unchanged from │    │
│ │  securityContext:        │   │  Phase 4               │    │
│ │   runAsNonRoot, RO-FS    │   │                        │    │
│ │   dropCaps: [ALL]        │   │                        │    │
│ │  probes: GET /healthz    │   │                        │    │
│ └──────────────────────────┘   └──────┬─────────────────┘    │
│                                       │                      │
│                   ┌───────────────────┼───────────────┐      │
│                   ▼                   ▼               ▼      │
│            ┌──────────────┐   ┌──────────────┐  ┌────────┐   │
│            │ PostgreSQL   │   │ MLflow       │  │ Redis  │   │
│            │ (Phase 2)    │   │ (Phase 4)    │  │(Phase 2│   │
│            └──────────────┘   └──────────────┘  └────────┘   │
└──────────────────────────────────────────────────────────────┘
         │                                         ▲
         ▼                                         │
┌──────────────────────┐                ┌──────────────────────┐
│ Harbor (subchart)    │◄───build+push──│ dev machine (pnpm)   │
│ lolday/lolday-       │                │ pnpm build → Docker  │
│ frontend:phaseN      │                │ image → docker push  │
└──────────────────────┘                └──────────────────────┘

Auth flow (cookie):
  1. POST /api/v1/auth/cookie/login (form-data username + password)
       → 204 + Set-Cookie: lolday_session=<JWT>; HttpOnly; Secure;
                            SameSite=Lax; Path=/; Max-Age=43200
  2. Any subsequent request: browser auto-sends cookie (same origin).
  3. 401 response → frontend router redirects to /login and clears cache.
  4. POST /api/v1/auth/cookie/logout → backend issues
       Set-Cookie: lolday_session=; Max-Age=0 (clear).

Live-data flow (no SSE):
  TanStack Query `refetchInterval: 2000` while job/build status is
  non-terminal (pending/preparing/running/scanning/building). On
  terminal status, interval is set to `false` (stop polling) and
  status-dependent badges flip to their final state.
```

---

## Screen Inventory

Each screen is listed with its route, primary components, data dependencies (TanStack Query keys), and key interactions. Routes follow React Router v7 file-convention style.

### 1. Auth — `/login`

- **Layout:** centered card on blank background (no sidebar).
- **Components:** `LoginForm` (react-hook-form + zod). Email + password.
- **Flow:** On submit, `POST /api/v1/auth/cookie/login`. On 204 success, invalidate QueryClient cache + `navigate("/")`. On 400 (invalid creds), show inline `Alert` with backend error. On 429 (rate-limited), show cooldown message.
- **Logout** is a button in the sidebar footer → `POST /api/v1/auth/cookie/logout` → clear cache → `navigate("/login")`.
- **Route guard:** private routes wrapped in `<AuthedLayout />`, which calls `useCurrentUser()` (GET `/api/v1/users/me`) and redirects on 401.

### 2. Profile — `/profile`

- **Sections:** basic info (email, role — read-only), change password form, Git credential manager.
- **Git credential:** three states surfaced — not set / set (masked) / expired (backend signals via 401 on build). Buttons: Set / Update / Clear.
- **Data:** `GET /api/v1/users/me`, `GET /api/v1/users/me/git-credential`.
- **Mutations:** `PATCH /api/v1/users/me` (password), `PUT /api/v1/users/me/git-credential`, `DELETE /api/v1/users/me/git-credential`.

### 3. Detectors — `/detectors`, `/detectors/new`, `/detectors/:id`

- **List (`/detectors`)**
  - Components: `TanStack Table` with columns display_name, description, owner, latest version, created_at, actions (view / delete).
  - Data: `GET /api/v1/detectors` paginated (client drives page + page_size).
  - Actions: `[+ Register detector]` button → `/detectors/new`.

- **Register (`/detectors/new`)**
  - Components: `RegisterDetectorForm` — name (slug, unique), display_name, git_url (input), description (textarea). No git_tag here — tags are listed on detail page.
  - Submit: `POST /api/v1/detectors`. On success → `/detectors/:id`.

- **Detail (`/detectors/:id`)**
  - Tabs: **Overview** | **Versions** | **Builds**.
  - Overview: metadata card (display_name, name, description, git_url, owner, created_at), delete button (admin / owner only).
  - Versions: table (git_tag, git_sha, status, built_at). Click row → drawer with full `config_schema` JSON viewer.
  - Builds: table (git_tag, status, started_at, finished_at, actions). Rows with non-terminal status auto-refresh every 2s. Action: view build log tail (drawer with `<pre>` tail). Action: cancel (non-terminal only). `[+ Trigger build]` opens dialog → pick git tag from `GET /detectors/:id/available-tags` → `POST /detectors/:id/builds`.
  - Data keys: `detectors.detail(id)`, `detectors.versions(id)`, `detectors.builds(id)`, `detectors.availableTags(id)`.

### 4. Datasets — `/datasets`, `/datasets/new`, `/datasets/:id`

- **List (`/datasets`)**
  - Columns: name, visibility (public/private badge), sample_count, size, owner, created_at, actions. Owner-scoped by default; toggle "include public" to see peers'.
  - Data: `GET /api/v1/datasets?visibility=public|private|all`.

- **Upload (`/datasets/new`)**
  - Components: `DatasetUploadForm` with **file picker (drag-drop zone) OR textarea paste** — mutually exclusive (switching clears the other). Client reads file via `File.text()` → body `csv_content`. Max 10 MB (hard-check in browser before POST).
  - Fields: name, description, visibility, csv_content.
  - Submit: `POST /api/v1/datasets`. Preview table (first 20 rows) rendered client-side before submit — if parse fails, disable submit with inline error.

- **Detail (`/datasets/:id`)**
  - Components: metadata card, `Recharts PieChart` for label_distribution, `Recharts BarChart` for family_distribution (top 15), CSV preview (first 100 rows, collapsible), `[Download CSV]` via `GET /datasets/:id/csv`.
  - Delete button (owner / admin only) → confirm → `DELETE /datasets/:id`.

### 5. Jobs — `/jobs`, `/jobs/new`, `/jobs/:id`

- **List (`/jobs`)**
  - Columns: type (badge), status (colored badge), detector/version, datasets (icons tooltipped), submitted_at, duration, owner. Filter bar: type, status, owner-scope. Auto-refresh rows with non-terminal status every 2s.
  - Data: `GET /api/v1/jobs?type=...&status=...&owner=...` paginated.

- **Submit (`/jobs/new`) — THE critical screen**
  - **Layout:** single page, vertically stacked sections with progressive disclosure based on `type`:

    ```
    ┌─ Job type ─────────────────────────────┐
    │ [Train] [Evaluate] [Predict]            │  (segment control)
    └─────────────────────────────────────────┘
    ┌─ Detector ─────────────────────────────┐
    │ Detector: <combobox>                    │
    │ Version:  <combobox (depends on detector)>
    └─────────────────────────────────────────┘
    ┌─ Data ─────────────────────────────────┐
    │ (type=train)   train_dataset + test_dataset
    │ (type=evaluate) test_dataset + source_model
    │ (type=predict)  predict_dataset + source_model
    └─────────────────────────────────────────┘
    ┌─ Config (RJSF) ───────────────────────┐
    │ fetched from version.config_schema     │
    │ rendered with rjsf-tailwind theme       │
    │ validate on blur + on submit            │
    └─────────────────────────────────────────┘
    [Cancel] [Submit job]
    ```

  - **State management:** react-hook-form for the top-level form (type, detector_id, version_id, dataset refs, source_model_version_id). The RJSF subtree renders into a controlled field `resolved_config` — its schema comes from `detectors.versions(...)` query and switches when `version_id` changes.
  - **Validation:** zod schemas verify required cross-refs per `type`. RJSF handles the config subtree via its own ajv validator (schema is Draft 7 — backend normalizes from Pydantic v2 Draft 2020-12 during build).
  - **Submit:** `POST /api/v1/jobs` with `{type, detector_version_id, train_dataset_id?, test_dataset_id?, predict_dataset_id?, source_model_version_id?, params: resolved_config}`. On success, navigate to `/jobs/:id`.
  - **Clone from previous run:** job detail page has `[Clone]` button that prefills `/jobs/new` with the previous job's config via query string `?from=<job_id>`.

- **Detail (`/jobs/:id`)**
  - Header: type badge, status badge, duration, owner, `[Cancel]` button (non-terminal only).
  - Tabs: **Summary** | **Logs** | **Artifacts** | **Run in MLflow ↗** (disabled until `mlflow_run_id` populated).
  - Summary: metadata card + metrics card (accuracy/precision/recall/F1 when available) + confusion matrix HTML heatmap (when `summary_metrics.confusion_matrix` exists) + resolved_config JSON viewer.
  - Logs: `<pre>` tail view of `GET /jobs/:id/logs`. Auto-refresh every 2s while non-terminal. `[Download full logs]` button → same endpoint, full content.
  - Artifacts: recursive tree view of `GET /runs/:run_id/artifacts?path=...` (only when job has a run), each leaf has download link → `GET /runs/:run_id/artifacts/download?path=...`.

### 6. Runs — `/runs`, `/runs/:experimentId`, `/runs/:experimentId/:runId`

- **Experiment list (`/runs`)**
  - Data: `GET /api/v1/experiments` — one row per MLflow experiment (one per detector). Click to drill in.

- **Run list (`/runs/:experimentId`)**
  - Columns: run_id (short), run_name, status, started_at, duration, key metrics (accuracy, f1). Link to job if `tags.lolday_job_id` present.
  - Data: `GET /api/v1/experiments/:id/runs`.

- **Run detail (`/runs/:experimentId/:runId`)**
  - Sections: metadata card, params table, metrics table, tags table, **confusion matrix HTML heatmap** (if `confusion_matrix.json` artifact present — fetched client-side), artifact tree + download.
  - Data: `GET /api/v1/runs/:runId`, `GET /api/v1/runs/:runId/artifacts`.
  - "View as MLflow UI ↗" link placeholder (enabled when Phase 6 ships MLflow external route).

### 7. Models — `/models`, `/models/:name`

- **List (`/models`)**
  - Columns: name (mlflow_name), latest_version, production_version, staging_version, owner, last_transitioned_at.
  - Data: `GET /api/v1/models`.

- **Detail (`/models/:name`)**
  - Versions table: mlflow_version, current_stage (badge: Staging/Production/Archived), source_run (link to `/runs/.../...` via mlflow_run_id), created_at, actions (Transition / Delete).
  - `[Transition]` opens dialog — choose target stage; if target is Production and another version is already Production, backend auto-archives current prod and dialog shows warning. `POST /api/v1/models/:name/versions/:version/transition`.
  - Delete (archive-only) → `DELETE /api/v1/models/:name/versions/:version` (admin / owner only).

---

## Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Build tool | Vite 5 | SPA standard; instant HMR |
| Language | TypeScript 5.5 | Type safety end-to-end with codegen |
| Package manager | pnpm 10 | Already on dev machine (master spec §13) |
| UI framework | React 18 | Master spec §2 decision |
| Router | React Router v7 (data APIs) | GitHub-mainstream; file-convention routes |
| Data fetching | TanStack Query v5 | REST de-facto; cache + refetch + optimistic |
| HTTP client + types | `openapi-fetch` + `openapi-typescript` | Types auto-synced with backend `openapi.json`; thin wrapper |
| Component library | shadcn/ui (copy-paste, Tailwind + Radix) | Owned in-repo; no China-origin; GitHub-mainstream 2024-2026 |
| Table | TanStack Table | Headless; W&B / Linear / GitHub use it |
| Forms (static) | react-hook-form + zod | GitHub-mainstream; type-safe schemas |
| Forms (dynamic) | `@rjsf/core` + `@rjsf/validator-ajv8` + `rjsf-tailwind` theme | Master spec §2 decision; schema comes from backend |
| Charts | Recharts | ~25k★ React lib; bar / line / pie sufficient for metrics |
| Styling | Tailwind CSS v4 | shadcn dependency |
| Icons | `lucide-react` | shadcn default |
| Dates | `date-fns` | Used for submitted_at, duration formatting |
| i18n | `react-i18next` | `en.json` primary; `zh-TW.json` scaffold |
| Testing (unit) | Vitest + React Testing Library | Vite-native |
| Testing (E2E) | Playwright | One happy-path per screen; CI-friendly |
| Container | nginx 1.27-alpine | Static file serving + SPA fallback |

**Deliberately excluded:**
- No Redux / Zustand — TanStack Query owns server state; react-hook-form owns form state; React Context covers user session. No Flux-style store needed at MVP scope.
- No CSS-in-JS (Emotion, styled-components) — Tailwind covers styling.
- No Storybook — component catalog is a deferral.
- No Sentry / error-reporting SaaS — Phase 6 adds Loki-based frontend error capture.
- No service worker / PWA — internal tool, online-only.

---

## Authentication

### Backend change (only change to Phase 2–4 backend)

Add a second `AuthenticationBackend` using `CookieTransport` alongside the existing `BearerTransport`. FastAPI Users supports multiple backends; both can be registered on the auth router and clients pick their transport by endpoint.

```python
# app/users.py
from fastapi_users.authentication import CookieTransport

cookie_transport = CookieTransport(
    cookie_name="lolday_session",
    cookie_max_age=settings.COOKIE_LIFETIME_SECONDS,  # default 43200 = 12h
    cookie_httponly=True,
    cookie_secure=True,          # HTTPS-only; dev uses HTTP → set False via env
    cookie_samesite="lax",
    cookie_path="/",
    cookie_domain=None,          # default: same-origin
)

cookie_auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend, cookie_auth_backend],   # bearer still works for curl / CI
)
```

```python
# app/main.py — mount both auth routers under the same prefix
app.include_router(
    fastapi_users.get_auth_router(cookie_auth_backend),
    prefix="/api/v1/auth/cookie",   # POST /cookie/login, /cookie/logout
    tags=["auth"],
)
```

### Browser flow

1. Unauthenticated visit to `/` → route guard sees `GET /api/v1/users/me` → 401 → redirect to `/login`.
2. Login → `POST /api/v1/auth/cookie/login` with form-encoded `username`, `password`.
3. Backend responds `204 No Content` + `Set-Cookie: lolday_session=<JWT>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=43200`.
4. Router `navigate("/")`. QueryClient is invalidated so user, navigation, everything refetches.
5. Subsequent requests: browser auto-sends cookie (same origin).
6. On any 401 from a protected endpoint: intercept in `openapi-fetch` middleware, clear QueryClient, `navigate("/login")`. This handles session expiry mid-session gracefully.
7. Logout: `POST /api/v1/auth/cookie/logout` → backend issues `Set-Cookie: lolday_session=; Max-Age=0` → clear cache → navigate to `/login`.

### Why cookie, not localStorage

- XSS-hardened: `HttpOnly` prevents JavaScript access; an XSS bug can't exfiltrate the session.
- Same-origin simplifies cookie setup: no CORS preflights, no `credentials: "include"` ceremony beyond the default.
- `SameSite=Lax` blocks cross-site POSTs (sufficient CSRF defense for an internal tool with no external integrations).
- Security research lab value alignment.

### Session expiry (12 h sliding)

- `JWT_LIFETIME_SECONDS` and `COOKIE_LIFETIME_SECONDS` both default to `43200` (12 h).
- Cookie is refreshed on every authed request by FastAPI Users (new `Set-Cookie` header). Active users stay logged in indefinitely; idle sessions expire at 12 h.
- No refresh-token rotation — one cookie, one lifetime, keep-alive via activity.

### Rate limiting

Existing slowapi config covers `/auth/*`. Frontend surfaces 429 as an `Alert` on the login page with a brief cooldown message.

---

## Data Fetching & Caching

### Type generation pipeline

```bash
# scripts/gen-api-types.sh
pnpm exec openapi-typescript \
  http://localhost:8000/openapi.json \
  -o src/api/schema.gen.ts
```

- Run manually during dev; committed to repo (not checked into CI of backend — frontend is a separate workflow).
- `openapi-fetch` consumes `schema.gen.ts` for typed `GET/POST/...` calls.

### Query organization

```ts
// src/api/queries/detectors.ts
export const detectorsKeys = {
  all: ["detectors"] as const,
  list: (params: ListParams) => [...detectorsKeys.all, "list", params] as const,
  detail: (id: string) => [...detectorsKeys.all, "detail", id] as const,
  versions: (id: string) => [...detectorsKeys.all, "versions", id] as const,
  builds: (id: string) => [...detectorsKeys.all, "builds", id] as const,
};

export function useDetectors(params: ListParams) {
  return useQuery({
    queryKey: detectorsKeys.list(params),
    queryFn: () => client.GET("/detectors", { params: { query: params } }),
  });
}
```

Keys use hierarchical arrays for surgical invalidation — `invalidateQueries({queryKey: detectorsKeys.all})` refreshes everything under detectors after a mutation.

### Polling

TanStack Query `refetchInterval` driven by a status predicate:

```ts
useQuery({
  queryKey: jobsKeys.detail(id),
  queryFn: () => client.GET("/jobs/{job_id}", { params: { path: { job_id: id } } }),
  refetchInterval: (query) =>
    isNonTerminalStatus(query.state.data?.data?.status) ? 2000 : false,
});
```

Applies to: `jobs/:id`, `jobs/:id/logs`, `detectors/:id/builds`, `detectors/:id/builds/:buildId`.

### Mutations + cache invalidation

```ts
useMutation({
  mutationFn: (body) => client.POST("/detectors", { body }),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: detectorsKeys.all }),
});
```

### Error handling

- `openapi-fetch` middleware wraps fetch; on non-2xx it extracts `detail` from FastAPI's default error envelope and throws `LoldayApiError` carrying status + detail + field errors (for 422).
- Global error boundary at router root renders a fallback page.
- 401 handled in middleware (see Auth section).
- 422 validation errors are mapped to react-hook-form `setError("field", ...)` in `onError` callbacks.
- Other errors surface as shadcn `Toast` notifications.

---

## Form Handling

### Static forms — react-hook-form + zod

```ts
const schema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
});
const form = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });
```

Applied to: login, change-password, register-detector, upload-dataset, profile, model transition dialog.

### Dynamic form — RJSF for detector config

```tsx
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import { ThemeProvider } from "rjsf-tailwind";

<ThemeProvider>
  <Form
    schema={version.config_schema}   // fetched from backend detectorVersion detail
    validator={validator}
    formData={currentConfig}
    onChange={({formData}) => setResolvedConfig(formData)}
    onSubmit={null}                   // submission handled by parent
    uiSchema={{ "ui:submitButtonOptions": { norender: true } }}
  />
</ThemeProvider>
```

- The backend already normalizes Pydantic v2 Draft 2020-12 → Draft 7 during build (main spec §4.4), so `@rjsf/validator-ajv8` can consume the stored `config_schema` directly.
- RJSF's form data feeds a controlled input on the parent react-hook-form; the parent's `handleSubmit` merges `{type, detector_version_id, ...datasets, params: resolvedConfig}` and POSTs.

---

## Real-time / Polling

No SSE, no WebSocket. Polling only — matches backend `GET /jobs/{id}/logs` which already returns a snapshot (not a stream).

| Resource | Interval | Stop condition |
|----------|----------|----------------|
| Job status / logs / individual | 2 s | Terminal: `succeeded / failed / cancelled / timeout` |
| Build status / log tail | 2 s | Terminal: `success / failed / cancelled` |
| Job list (filtered to non-terminal) | 5 s | None — always running when list is visible |

A custom hook `usePolling(queryKey, queryFn, isActive)` wraps this pattern.

---

## Directory Structure

```
frontend/
├── Dockerfile                  # multi-stage: node build → nginx serve
├── nginx.conf                  # SPA fallback; cache headers; gzip
├── package.json
├── pnpm-lock.yaml
├── tsconfig.json
├── vite.config.ts
├── tailwind.config.ts
├── postcss.config.js
├── components.json             # shadcn registry config
├── playwright.config.ts
├── vitest.config.ts
├── index.html
├── public/
│   └── favicon.svg
├── scripts/
│   └── gen-api-types.sh
├── src/
│   ├── main.tsx
│   ├── App.tsx                 # router tree + QueryClient + ThemeProvider + I18nProvider
│   ├── routes/                 # React Router v7 file-convention
│   │   ├── _public.tsx         # layout: blank, centered
│   │   ├── _authed.tsx         # layout: sidebar + breadcrumb
│   │   ├── _authed._index.tsx  # redirect → detectors
│   │   ├── _authed.detectors._index.tsx
│   │   ├── _authed.detectors.new.tsx
│   │   ├── _authed.detectors.$id.tsx
│   │   ├── _authed.datasets._index.tsx
│   │   ├── _authed.datasets.new.tsx
│   │   ├── _authed.datasets.$id.tsx
│   │   ├── _authed.jobs._index.tsx
│   │   ├── _authed.jobs.new.tsx
│   │   ├── _authed.jobs.$id.tsx
│   │   ├── _authed.runs._index.tsx
│   │   ├── _authed.runs.$expId.tsx
│   │   ├── _authed.runs.$expId.$runId.tsx
│   │   ├── _authed.models._index.tsx
│   │   ├── _authed.models.$name.tsx
│   │   ├── _authed.profile.tsx
│   │   └── _public.login.tsx
│   ├── api/
│   │   ├── schema.gen.ts       # openapi-typescript output
│   │   ├── client.ts           # openapi-fetch client w/ cookie creds + middleware
│   │   ├── errors.ts           # LoldayApiError + status mapping
│   │   └── queries/
│   │       ├── auth.ts
│   │       ├── users.ts
│   │       ├── detectors.ts
│   │       ├── datasets.ts
│   │       ├── jobs.ts
│   │       ├── runs.ts
│   │       └── models.ts
│   ├── components/
│   │   ├── ui/                 # shadcn primitives (button, card, dialog, input, ...)
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── TopBar.tsx
│   │   │   └── Breadcrumb.tsx
│   │   ├── tables/             # DataTable wrapper around TanStack Table
│   │   ├── forms/
│   │   │   ├── LoginForm.tsx
│   │   │   ├── RegisterDetectorForm.tsx
│   │   │   ├── DatasetUploadForm.tsx
│   │   │   ├── JobSubmitForm.tsx
│   │   │   └── RjsfConfigForm.tsx
│   │   └── charts/
│   │       ├── MetricCards.tsx
│   │       ├── ConfusionMatrix.tsx    # HTML + Tailwind heatmap
│   │       ├── LabelDistribution.tsx  # Recharts PieChart
│   │       └── FamilyDistribution.tsx # Recharts BarChart
│   ├── hooks/
│   │   ├── useAuth.ts          # current user, logout
│   │   ├── usePolling.ts       # generic 2s poller
│   │   └── useBreadcrumb.ts    # derive from route match
│   ├── lib/
│   │   ├── csv.ts              # client-side preview parser
│   │   ├── date.ts             # formatDuration, formatRelative
│   │   ├── status.ts           # status → color + label mapping
│   │   └── errors.ts           # API error → form field errors
│   ├── i18n/
│   │   ├── index.ts            # i18next setup
│   │   ├── en.json             # primary
│   │   └── zh-TW.json          # scaffold (empty keys)
│   └── types/
│       └── domain.ts           # aliases over schema.gen.ts
├── tests/
│   ├── unit/
│   │   ├── components/
│   │   ├── hooks/
│   │   └── lib/
│   └── e2e/
│       ├── login.spec.ts
│       ├── detector-build.spec.ts
│       ├── dataset-upload.spec.ts
│       ├── job-train.spec.ts
│       └── model-transition.spec.ts
└── .env.example                # VITE_API_BASE=/api/v1
```

---

## Deployment

### Dockerfile

```dockerfile
# ---- build stage ----
FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm run build

# ---- serve stage ----
# nginxinc/nginx-unprivileged is pre-configured for non-root + listen 8080
# + pid/cache/tmp under /tmp. Works out of the box with readOnlyRootFilesystem.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK CMD wget -q --spider http://127.0.0.1:8080/healthz || exit 1
```

### nginx.conf

```nginx
server {
  listen 8080 default_server;
  server_name _;
  root /usr/share/nginx/html;

  # SPA fallback — any unmatched route returns index.html for React Router
  location / {
    try_files $uri $uri/ /index.html;
  }

  # Healthcheck for kubelet probes
  location = /healthz { return 200 "ok"; }

  # Cache busting: index.html never cached, assets cached aggressively (hashed names)
  location = /index.html { add_header Cache-Control "no-store"; }
  location ~* \.(js|css|woff2|svg|png|ico)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
  }

  gzip on;
  gzip_types text/css application/javascript image/svg+xml;
}
```

### K8s resources (new Helm template)

`charts/lolday/templates/frontend.yaml` — single template with Deployment + Service:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: frontend, namespace: lolday }
spec:
  replicas: 1
  selector: { matchLabels: { app: frontend } }
  template:
    metadata: { labels: { app: frontend } }
    spec:
      containers:
        - name: nginx
          image: "{{ .Values.frontend.image }}"     # harbor.lolday.svc:80/lolday/lolday-frontend:phase5
          imagePullPolicy: IfNotPresent
          ports: [{ containerPort: 8080 }]
          readinessProbe: { httpGet: { path: /healthz, port: 8080 }, periodSeconds: 5 }
          livenessProbe:  { httpGet: { path: /healthz, port: 8080 }, periodSeconds: 10 }
          resources:
            requests: { cpu: 10m, memory: 32Mi }
            limits:   { cpu: 100m, memory: 128Mi }
          securityContext:
            runAsNonRoot: true
            readOnlyRootFilesystem: true
            allowPrivilegeEscalation: false
            capabilities: { drop: [ALL] }
            seccompProfile: { type: RuntimeDefault }
          volumeMounts:
            - { name: tmp,      mountPath: /tmp }        # nginx-unprivileged writes pid + cache here
      volumes:
        - { name: tmp, emptyDir: {} }
      imagePullSecrets:
        - name: harbor-pull-cred
---
apiVersion: v1
kind: Service
metadata: { name: frontend, namespace: lolday }
spec:
  selector: { app: frontend }
  ports: [{ port: 80, targetPort: 8080 }]
```

### Traefik ingress (new template)

`charts/lolday/templates/ingress.yaml` — single `IngressRoute` (Traefik CRD, shipped with K3s):

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata: { name: lolday, namespace: lolday }
spec:
  entryPoints: [web]                            # Phase 6 adds websecure
  routes:
    - kind: Rule
      match: "Host(`{{ .Values.frontend.host }}`) && PathPrefix(`/api/v1`)"
      services:
        - kind: Service
          name: backend
          port: 8000
    - kind: Rule
      match: "Host(`{{ .Values.frontend.host }}`)"  # catch-all
      services:
        - kind: Service
          name: frontend
          port: 80
```

Default host: `lolday.islab.local`. Because the lab LAN has no DNS entry for this hostname, each developer machine adds `<server30-LAN-IP>  lolday.islab.local` to its `/etc/hosts`. (Same pattern already used for `harbor.lolday.svc.cluster.local` in Phase 3.) Phase 6 replaces this with a real Cloudflare-backed FQDN.

### Helm values additions

```yaml
# charts/lolday/values.yaml
frontend:
  image: harbor.lolday.svc:80/lolday/lolday-frontend:phase5
  host: lolday.islab.local
```

### Build + push pipeline

Same rhythm as Phase 3/4:
```bash
docker build -t harbor.lolday.svc.cluster.local:80/lolday/lolday-frontend:phase5 frontend/
docker push  harbor.lolday.svc.cluster.local:80/lolday/lolday-frontend:phase5
FRONTEND_IMAGE=harbor.lolday.svc:80/lolday/lolday-frontend:phase5 bash scripts/deploy.sh
```

`scripts/deploy.sh` gains a `FRONTEND_IMAGE` env handle mirroring the existing `BACKEND_IMAGE` one — both are passed into Helm via `--set frontend.image=...` / `--set backend.image=...`.

No auto-build (yet). Phase 5 is still developer-push cycle; Phase 6 considers adding a GitHub Action or Tekton pipeline.

---

## Testing Strategy

### Unit (Vitest + React Testing Library)

Scope: lib utilities, pure hooks, form schemas, status→color maps, CSV parser, API error mapping. Component tests cover critical rendering logic only (e.g., JobSubmitForm's conditional field rendering per type) — full DOM behavior is covered by E2E.

Target: ≥60% line coverage on `src/lib/**` and `src/hooks/**`. Visual components (charts, tables) are not line-coverage-chased; trust Storybook-style smoke tests if we add them later.

### E2E (Playwright)

Headless Chromium against a freshly deployed stack (local port-forward or in-cluster e2e). Each spec is a happy path — not exhaustive.

| Spec | Flow |
|------|------|
| `login.spec.ts` | Visit `/`, redirected to `/login`, submit valid creds, land on detectors. |
| `detector-build.spec.ts` | Register upxelfdet from its real GitHub repo → pick available tag `v0.5.0` → trigger build → poll until `success`. Reuses Phase 3 E2E's seed PAT. |
| `dataset-upload.spec.ts` | Navigate to `/datasets/new`, upload a 200-row test CSV, verify detail page shows correct `sample_count` + pie chart. |
| `job-train.spec.ts` | Submit a train job with seed dataset + detector version, wait for terminal status, verify MLflow run visible on `/runs/...`. |
| `model-transition.spec.ts` | Pick an existing model version, transition Staging → Production, verify auto-archive of previous prod. |

E2E bootstrap reuses Phase 4 E2E seed (admin login via cookie flow + pre-seeded detector + dataset). Runs gated behind `E2E_ENABLED=true` — not part of default `pnpm test`.

### Where E2E runs

Default: local dev machine against port-forwarded cluster — same pattern as Phase 4 E2E checklist. A hosted CI pipeline is a Phase 6 concern.

---

## Security

| Layer | Measure | Implementation |
|-------|---------|----------------|
| Auth | httpOnly cookie, SameSite=Lax | FastAPI Users `CookieTransport` |
| Auth expiry | 12 h sliding | JWT + cookie lifetime |
| CSRF | SameSite=Lax blocks cross-site POST | No additional CSRF token needed for same-origin internal |
| XSS | React escapes by default; no `dangerouslySetInnerHTML` in core screens | Code review rule |
| Content-Security-Policy | `default-src 'self'` + nonce for styles; no inline script | Set via nginx `add_header CSP` (Phase 6: refine with Cloudflare) |
| Clickjacking | `X-Frame-Options: DENY` | nginx header |
| MIME sniffing | `X-Content-Type-Options: nosniff` | nginx header |
| Secrets in bundle | `import.meta.env` only surfaces `VITE_*` vars; no backend secrets | vite convention |
| Dependency audit | `pnpm audit` in CI | ship if ≥1 high CVE |
| Container | non-root, read-only FS, dropped caps, seccomp | As in Phase 4 jobs |
| Network | frontend has no egress requirement; only serves static bundle | No NetworkPolicy needed beyond default deny-ingress-from-outside |

Security posture defers Cloudflare Access / Tunnel to Phase 6; Phase 5 Ingress is internal-only on the lab network.

---

## i18n

- Library: `react-i18next` with namespace-less keys (`t("detectors.list.title")`).
- Languages: `en` (primary, complete). `zh-TW.json` is committed as `{}` — `react-i18next` transparently falls back to `en` for missing keys, so the app runs identically whether or not zh-TW is populated. Phase 6 (or a volunteer translator) fills it in later.
- Detection: `i18next-browser-languagedetector` with hard default `en` — user can switch via profile dropdown (stores preference in localStorage).
- Dates: `date-fns` with locale import per language.
- Numbers / percentages: `Intl.NumberFormat` — no library.

Localizable surfaces: all UI text, status labels, error messages. Not localized: backend error `detail` strings (English always — backend messages are developer-facing), detector / dataset / run names (user-generated).

---

## Error Handling

| Status | Behavior |
|--------|----------|
| 401 | `openapi-fetch` middleware clears QueryClient, navigates to `/login`. No toast (implicit). |
| 403 | Toast: "You don't have permission for this action." Keep user on current page. |
| 404 | If the whole page's primary resource 404s, render a 404 panel with back link. Query-level 404s render `Alert` inline. |
| 409 | Toast with backend detail (e.g., "Job already completed"). |
| 413 | Client-side pre-check catches 10 MB dataset upload; if server still 413s, inline alert on form. |
| 422 | Parse `detail: List[{loc, msg}]` → `form.setError("field", msg)` for inputs; residual goes to form-level alert. |
| 429 | Toast with "Rate limited, retry in N seconds." Disable affected action for 10 s (client heuristic). |
| 5xx | Toast: "Server error — try again or contact admin." Error boundary captures unhandled exceptions + shows fallback page with reload button. |
| Network (offline / DNS fail) | Toast: "Network error." Retry button where action is a mutation. |

No Sentry / Rollbar in Phase 5. Structured client-side error logging is Phase 6 (via Loki + structured console log forwarder).

---

## Open Questions / Risks

1. **RJSF + shadcn visual cohesion** — `rjsf-tailwind` is less polished than the first-party MUI/AntD themes. We may need a thin custom theme layer if fields look off; budget ~0.5 day for styling tuning.
2. **Artifact tree scalability** — `GET /runs/:id/artifacts?path=...` returns one level; deep trees recurse on expand. MLflow artifacts with 10k+ files could be slow; for MVP, detectors produce <50 artifacts so this isn't a problem. Guard with a hard-coded depth limit of 10 anyway.
3. **Large CSV upload UX** — a 9.9 MB file takes >1 s to `File.text()` + JSON-encode + POST. Show progress via `fetch` upload progress (native) + shadcn `Progress` bar. Acceptable.
4. **Detector config schema complexity** — RJSF can choke on deeply-nested `anyOf` / `$ref` chains. Phase 3 upxelfdet schema is tested flat; if a future detector uses complex schemas, add a JSON editor fallback (textarea with ajv validation). Out of scope for Phase 5.
5. **Cookie flag `Secure` in dev** — dev machine runs HTTP (not HTTPS). Cookie with `Secure` won't be set. Workaround: `COOKIE_SECURE=false` in dev env; `true` in cluster (Traefik terminates TLS at Phase 6 Cloudflare).
6. **Single `lolday_session` cookie name across environments** — avoids mix-ups; if developer logs into dev cluster and then hits prod on same host, they'd need to log in again (different host → different cookie jar entry, so no real conflict).
7. **Traefik IngressRoute CRD availability** — K3s ships Traefik 2.x by default. Verify `traefik.io/v1alpha1` is installed; fallback to standard `Ingress` with `traefik.ingress.kubernetes.io/router.priority` annotation if CRD isn't registered.

---

## Prerequisites & New Tooling

Already installed (master spec §13): Node.js 24, npm 11, pnpm 10, Docker 29, kubectl, Helm.

To install / add during Phase 5:
- Playwright browsers: `pnpm exec playwright install chromium` (~180 MB, one-time).
- No new host-level dependencies.

---

## Success Criteria

Phase 5 is done when:

1. **Deploy:** `bash scripts/deploy.sh` (with `FRONTEND_IMAGE` built + pushed) brings up a working frontend Deployment reachable at `http://lolday.islab.local/` from the lab LAN; `kubectl get pods -n lolday` shows all prior Phase 4 pods plus `frontend-*` `Running 1/1`.
2. **E2E:** all 5 Playwright specs pass against the deployed stack. A lab member can, from browser only:
   - Log in with admin credentials seeded from `~/.lolday-secrets.env`.
   - Set a GitHub PAT via profile.
   - Register upxelfdet, trigger a build of `v0.5.0`, watch it succeed within the build timeout.
   - Upload a small dataset CSV (100 rows), see stats render.
   - Submit a train job using the built version + dataset, watch status flip `pending → running → succeeded` with live logs.
   - Click through to the MLflow run detail and download `predictions.csv`.
   - Promote the resulting model version Staging → Production.
3. **No regressions:** `kubectl -n lolday port-forward svc/backend 8000:8000` + the Phase 4 E2E curl script still pass (Bearer transport preserved).
4. **SSH intact:** `ssh -p 9453 server30` still works throughout.
5. **Spec + plan docs** committed; Phase 5 squash-merged to `main`.

---

## Phase 5 → Phase 6 handoff

Items deferred to Phase 6 that Phase 5 pre-wires:
- `VITE_APP_VERSION` baked into bundle at build time (visible in sidebar footer) → enables Phase 6 version-based cache bust for Cloudflare.
- CSP nginx headers scaffolded → Phase 6 tightens with Cloudflare.
- `frontend.host` Helm value → Phase 6 swaps to `lolday.islab.example.com` with Cloudflare Tunnel + Access.
- Error boundary fallback page → Phase 6 adds Loki log forward hook.
- i18n `zh-TW.json` scaffold exists → Phase 6 fills in (translation is content work, not engineering).
