# Phase 5: Frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a React + TypeScript SPA that wraps the Phase 2–4 backend so lab members can complete the full Phase 4 E2E flow (login → register detector → build → upload dataset → submit job → watch logs → download predictions → promote model) in a browser only — no `curl`, no `kubectl`.

**Architecture:** Vite-built SPA served by `nginxinc/nginx-unprivileged` as a single-replica K8s Deployment in namespace `lolday`, exposed via a Traefik `IngressRoute` that routes `/api/v1/*` to the existing backend Service and everything else to the frontend Service. Auth uses FastAPI Users `CookieTransport` (added alongside the existing `BearerTransport` — only backend change in this phase). Data fetching uses TanStack Query + `openapi-fetch` with types generated from the backend's `openapi.json`. Forms use `react-hook-form + zod`; the detector config form uses `@rjsf/core` (schema comes from `detector_version.config_schema`). Live updates use TanStack Query `refetchInterval: 2000` driven by a status predicate — no SSE, no WebSocket.

**Tech Stack:** Vite 5, React 18, TypeScript 5.5, pnpm 10, React Router v7 (data APIs), TanStack Query v5, TanStack Table v8, shadcn/ui + Radix + Tailwind CSS v4, lucide-react, Recharts, react-hook-form + zod, @rjsf/core + @rjsf/validator-ajv8 + rjsf-tailwind, react-i18next, Vitest + React Testing Library, Playwright, nginx 1.27-alpine (unprivileged).

**Spec:** `docs/superpowers/specs/2026-04-19-phase5-frontend-design.md`

**Server:** server30 (Ubuntu 24.04, K3s v1.34.6+k3s1; Phase 4 stack deployed: backend `phase4`, MLflow, PostgreSQL, Harbor, Redis).

**Constraints:**

- `bolin8017` has no persistent sudo; give sudo commands to user to run (no sudo expected for Phase 5 — user-land only).
- CLI tools in `~/.local/bin/`; do NOT system-install anything without explicit approval.
- SSH (port 9453) must never be disrupted; K3s must remain running after every step.
- No Cilium / no CNI changes.
- Backend-side change is limited to adding `CookieTransport` + a `/api/v1/auth/cookie/*` router; existing Bearer flow must keep working (Phase 4 curl E2E is a regression gate).
- No destructive git operations; commit per task.

---

## File Structure

Frontend package (all new; top-level directory `frontend/`):

```
frontend/
├── .env.example                  # VITE_API_BASE=/api/v1 etc.
├── .gitignore                    # node_modules, dist, playwright-report
├── Dockerfile                    # multi-stage pnpm build → nginx-unprivileged serve
├── nginx.conf                    # SPA fallback + cache headers + /healthz
├── components.json               # shadcn/ui registry config
├── index.html
├── package.json
├── pnpm-lock.yaml                # (generated, committed)
├── playwright.config.ts
├── postcss.config.js
├── tailwind.config.ts
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
├── vitest.config.ts
├── public/
│   └── favicon.svg
├── scripts/
│   └── gen-api-types.sh          # pnpm exec openapi-typescript ...
├── src/
│   ├── main.tsx                  # bootstrap
│   ├── App.tsx                   # router + QueryClient + I18n + Theme providers
│   ├── index.css                 # tailwind directives + CSS variables
│   ├── routes/                   # React Router v7 file-convention
│   │   ├── _public.tsx           # layout: blank centered
│   │   ├── _public.login.tsx     # /login
│   │   ├── _authed.tsx           # layout: sidebar + topbar + <Outlet/>
│   │   ├── _authed._index.tsx    # / → redirect to /detectors
│   │   ├── _authed.profile.tsx
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
│   │   └── _authed.models.$name.tsx
│   ├── api/
│   │   ├── schema.gen.ts         # openapi-typescript output (committed)
│   │   ├── client.ts             # openapi-fetch + middleware (401, errors)
│   │   ├── errors.ts             # LoldayApiError class
│   │   └── queries/
│   │       ├── auth.ts
│   │       ├── users.ts
│   │       ├── detectors.ts
│   │       ├── datasets.ts
│   │       ├── jobs.ts
│   │       ├── runs.ts
│   │       └── models.ts
│   ├── components/
│   │   ├── ui/                   # shadcn primitives added via CLI
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── TopBar.tsx
│   │   │   └── Breadcrumb.tsx
│   │   ├── tables/
│   │   │   └── DataTable.tsx     # TanStack Table wrapper
│   │   ├── forms/
│   │   │   ├── LoginForm.tsx
│   │   │   ├── PasswordChangeForm.tsx
│   │   │   ├── GitCredentialForm.tsx
│   │   │   ├── RegisterDetectorForm.tsx
│   │   │   ├── DatasetUploadForm.tsx
│   │   │   ├── JobSubmitForm.tsx
│   │   │   ├── RjsfConfigForm.tsx
│   │   │   └── ModelTransitionDialog.tsx
│   │   ├── charts/
│   │   │   ├── MetricCards.tsx
│   │   │   ├── ConfusionMatrix.tsx       # HTML + Tailwind heatmap
│   │   │   ├── LabelDistribution.tsx     # Recharts PieChart
│   │   │   └── FamilyDistribution.tsx    # Recharts BarChart
│   │   └── common/
│   │       ├── StatusBadge.tsx
│   │       ├── JsonViewer.tsx
│   │       ├── ArtifactTree.tsx
│   │       └── LogTail.tsx
│   ├── hooks/
│   │   ├── useAuth.ts
│   │   ├── usePolling.ts
│   │   └── useBreadcrumb.ts
│   ├── lib/
│   │   ├── csv.ts                # preview parse
│   │   ├── date.ts               # formatDuration, formatRelative
│   │   ├── status.ts             # status → color/label
│   │   └── errors.ts             # API error → form field errors
│   ├── i18n/
│   │   ├── index.ts
│   │   ├── en.json               # primary
│   │   └── zh-TW.json            # {} — fallback to en
│   └── types/
│       └── domain.ts             # convenience aliases over schema.gen.ts
└── tests/
    ├── setup.ts                  # vitest globals
    ├── unit/
    │   ├── lib/
    │   │   ├── csv.test.ts
    │   │   ├── date.test.ts
    │   │   ├── status.test.ts
    │   │   └── errors.test.ts
    │   ├── hooks/
    │   │   └── usePolling.test.ts
    │   └── components/
    │       ├── JobSubmitForm.test.tsx
    │       ├── DatasetUploadForm.test.tsx
    │       └── ConfusionMatrix.test.tsx
    └── e2e/
        ├── fixtures/
        │   └── small-dataset.csv
        ├── helpers.ts                    # login helper, seed helper
        ├── login.spec.ts
        ├── detector-build.spec.ts
        ├── dataset-upload.spec.ts
        ├── job-train.spec.ts
        └── model-transition.spec.ts
```

Backend changes (minimal — cookie auth only):

```
backend/
├── app/
│   ├── config.py                 # MODIFY: add COOKIE_LIFETIME_SECONDS, COOKIE_SECURE
│   ├── users.py                  # MODIFY: add CookieTransport, cookie_auth_backend
│   └── main.py                   # MODIFY: mount /api/v1/auth/cookie/* router
└── tests/
    └── test_auth_cookie.py       # NEW
```

Helm chart additions:

```
charts/lolday/
├── values.yaml                   # MODIFY (+ frontend.image, frontend.host)
└── templates/
    ├── frontend.yaml             # NEW (Deployment + Service)
    └── ingress.yaml              # NEW (Traefik IngressRoute)
```

Scripts:

```
scripts/
└── deploy.sh                     # MODIFY: accept FRONTEND_IMAGE env
```

---

## Task Ordering Rationale

Phase 5 is strictly additive. The order below lets each task produce something runnable / testable:

1. **Tasks 1–5 (Foundation):** scaffold project, deps, config, Tailwind + shadcn init. Ends with `pnpm dev` showing a blank page.
2. **Task 6 (Backend cookie auth):** only backend change. Regression-gated by Phase 4 curl E2E.
3. **Tasks 7–11 (Core infrastructure):** API types + client, TanStack Query, i18n, error handling, router tree. Ends with a stub authed route that 401-redirects.
4. **Tasks 12–14 (Layout + guards):** Sidebar, TopBar, breadcrumb, route guard.
5. **Tasks 15–17 (Auth + login E2E):** login form + first passing Playwright spec.
6. **Task 18 (Profile):** password + git credential.
7. **Tasks 19–22 (Detectors):** list → register → detail → build trigger/polling; closes with `detector-build.spec.ts`.
8. **Tasks 23–25 (Datasets):** list → upload → detail; closes with `dataset-upload.spec.ts`.
9. **Tasks 26–31 (Jobs):** list → submit form (including RJSF) → detail with logs + artifacts; closes with `job-train.spec.ts`.
10. **Tasks 32–34 (Runs):** experiment list → runs list → run detail with confusion matrix.
11. **Tasks 35–36 (Models):** list → transitions; closes with `model-transition.spec.ts`.
12. **Tasks 37–40 (Deployment):** Dockerfile, K8s template, Traefik IngressRoute, Helm values + deploy.sh.
13. **Task 41 (Final E2E + regression):** build + push image, full deploy, all 5 Playwright specs green, Phase 4 curl E2E still passes.

Commits after every task. E2E specs are added incrementally so CI stays green as the SPA grows.

---

## Task 1: Scaffold frontend project

**Files:**

- Create: `frontend/package.json`
- Create: `frontend/.gitignore`
- Create: `frontend/.env.example`
- Create: `frontend/index.html`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/index.css`
- Create: `frontend/src/vite-env.d.ts`

- [ ] **Step 1: Create `frontend/` directory and `package.json`**

```bash
mkdir -p /home/bolin8017/Documents/repositories/lolday/frontend
cd /home/bolin8017/Documents/repositories/lolday/frontend
```

Write `frontend/package.json`:

```json
{
  "name": "lolday-frontend",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit",
    "lint": "eslint .",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:e2e": "playwright test",
    "gen-api-types": "bash scripts/gen-api-types.sh"
  },
  "engines": {
    "node": ">=22"
  }
}
```

- [ ] **Step 2: Create `.gitignore`**

Write `frontend/.gitignore`:

```
node_modules
dist
dist-ssr
.env
.env.local
playwright-report
playwright/.cache
test-results
coverage
*.log
*.tsbuildinfo
.DS_Store
```

- [ ] **Step 3: Create `.env.example`**

Write `frontend/.env.example`:

```
# Backend API base path (relative — same origin). Override for dev against port-forwarded backend.
VITE_API_BASE=/api/v1
# App version (baked at build time; shown in sidebar footer)
VITE_APP_VERSION=dev
```

- [ ] **Step 4: Create `index.html`**

Write `frontend/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <title>Lolday</title>
  </head>
  <body class="bg-background text-foreground antialiased">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `tsconfig.json` + `tsconfig.node.json`**

Write `frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "allowImportingTsExtensions": false,
    "noEmit": true,
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  },
  "include": ["src", "tests"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

Write `frontend/tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "skipLibCheck": true
  },
  "include": [
    "vite.config.ts",
    "vitest.config.ts",
    "playwright.config.ts",
    "tailwind.config.ts",
    "postcss.config.js"
  ]
}
```

> Note: `noEmit` was intentionally removed from `tsconfig.node.json` — `composite: true` requires the project to emit. The root `tsconfig.json` keeps `"noEmit": true` so `tsc --noEmit` still governs the main typecheck.

- [ ] **Step 6: Create `vite.config.ts`**

Write `frontend/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    host: "127.0.0.1",
    proxy: {
      // Dev-mode: proxy API to port-forwarded backend so cookie auth stays same-origin
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
```

- [ ] **Step 7: Create `src/main.tsx`, `src/App.tsx`, `src/index.css`, `src/vite-env.d.ts`**

Write `frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

Write `frontend/src/App.tsx` (placeholder; providers added in Task 7/8/11):

```tsx
export default function App() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-muted-foreground">lolday frontend bootstrapping…</p>
    </div>
  );
}
```

Write `frontend/src/index.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

Write `frontend/src/vite-env.d.ts`:

```ts
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE: string;
  readonly VITE_APP_VERSION: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
```

- [ ] **Step 8: Commit scaffold**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/
git commit -m "feat(frontend): scaffold Vite + React + TS project structure"
```

Expected: commit created. `pnpm` not installed yet — task 2 wires dependencies.

---

## Task 2: Install dependencies with pnpm

**Files:**

- Modify: `frontend/package.json`
- Create: `frontend/pnpm-lock.yaml` (generated)

- [ ] **Step 1: Ensure pnpm is available**

```bash
pnpm --version
```

Expected: `10.x.x` (per master spec §13). If not, `corepack enable && corepack prepare pnpm@latest --activate`.

- [ ] **Step 2: Add runtime dependencies**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm add react@^18.3.0 react-dom@^18.3.0 \
  react-router@^7 \
  @tanstack/react-query@^5 @tanstack/react-table@^8 \
  openapi-fetch@^0.13 \
  react-hook-form@^7 @hookform/resolvers@^3 zod@^3 \
  @rjsf/core@^5 @rjsf/utils@^5 @rjsf/validator-ajv8@^5 \
  recharts@^2 \
  lucide-react@^0.460 \
  date-fns@^4 \
  react-i18next@^15 i18next@^24 i18next-browser-languagedetector@^8 \
  clsx@^2 class-variance-authority@^0.7 tailwind-merge@^2 \
  @radix-ui/react-slot@^1 @radix-ui/react-dialog@^1 @radix-ui/react-dropdown-menu@^2 \
  @radix-ui/react-tabs@^1 @radix-ui/react-select@^2 @radix-ui/react-label@^2 \
  @radix-ui/react-tooltip@^1 @radix-ui/react-toast@^1 @radix-ui/react-separator@^1 \
  @radix-ui/react-progress@^1 @radix-ui/react-popover@^1 \
  cmdk@^1
```

- [ ] **Step 3: Add dev dependencies**

```bash
pnpm add -D typescript@~5.5 @types/react@^18 @types/react-dom@^18 @types/node@^22 \
  vite@^5 @vitejs/plugin-react@^4 \
  tailwindcss@^3.4 postcss@^8 autoprefixer@^10 \
  eslint@^9 @typescript-eslint/eslint-plugin@^8 @typescript-eslint/parser@^8 \
  eslint-plugin-react@^7 eslint-plugin-react-hooks@^5 eslint-plugin-react-refresh@^0.4 \
  vitest@^2 @testing-library/react@^16 @testing-library/jest-dom@^6 @testing-library/user-event@^14 jsdom@^25 \
  @playwright/test@^1.48 \
  openapi-typescript@^7
```

> Note: Tailwind v3 is chosen intentionally over v4 because shadcn/ui's generator currently targets v3. The spec's mention of "Tailwind v4" is aspirational; ship with v3 and upgrade when shadcn's v4 config lands. Tracking: https://github.com/shadcn-ui/ui

> Note (pnpm 10 security): pnpm 10 blocks postinstall scripts by default, including esbuild's binary download. Add the following to `package.json` so `vite` can run:
>
> ```json
> {
>   "pnpm": {
>     "onlyBuiltDependencies": ["esbuild"]
>   }
> }
> ```

- [ ] **Step 4: Verify install and typecheck**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm typecheck
```

Expected: no errors (App.tsx and main.tsx are trivially valid).

- [ ] **Step 5: Commit dependency manifest**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/package.json frontend/pnpm-lock.yaml
git commit -m "feat(frontend): install runtime + dev dependencies"
```

---

## Task 3: Configure Tailwind CSS + PostCSS

**Files:**

- Create: `frontend/tailwind.config.ts`
- Create: `frontend/postcss.config.js`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Create `tailwind.config.ts`**

Write `frontend/tailwind.config.ts`:

```ts
import type { Config } from "tailwindcss";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 2: Create `postcss.config.js`**

Write `frontend/postcss.config.js`:

```js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

- [ ] **Step 3: Replace `src/index.css` with shadcn theme variables**

Write `frontend/src/index.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 0 0% 100%;
    --foreground: 222.2 84% 4.9%;
    --card: 0 0% 100%;
    --card-foreground: 222.2 84% 4.9%;
    --popover: 0 0% 100%;
    --popover-foreground: 222.2 84% 4.9%;
    --primary: 222.2 47.4% 11.2%;
    --primary-foreground: 210 40% 98%;
    --secondary: 210 40% 96.1%;
    --secondary-foreground: 222.2 47.4% 11.2%;
    --muted: 210 40% 96.1%;
    --muted-foreground: 215.4 16.3% 46.9%;
    --accent: 210 40% 96.1%;
    --accent-foreground: 222.2 47.4% 11.2%;
    --destructive: 0 84.2% 60.2%;
    --destructive-foreground: 210 40% 98%;
    --border: 214.3 31.8% 91.4%;
    --input: 214.3 31.8% 91.4%;
    --ring: 222.2 84% 4.9%;
    --radius: 0.5rem;
  }

  .dark {
    --background: 222.2 84% 4.9%;
    --foreground: 210 40% 98%;
    --card: 222.2 84% 4.9%;
    --card-foreground: 210 40% 98%;
    --popover: 222.2 84% 4.9%;
    --popover-foreground: 210 40% 98%;
    --primary: 210 40% 98%;
    --primary-foreground: 222.2 47.4% 11.2%;
    --secondary: 217.2 32.6% 17.5%;
    --secondary-foreground: 210 40% 98%;
    --muted: 217.2 32.6% 17.5%;
    --muted-foreground: 215 20.2% 65.1%;
    --accent: 217.2 32.6% 17.5%;
    --accent-foreground: 210 40% 98%;
    --destructive: 0 62.8% 30.6%;
    --destructive-foreground: 210 40% 98%;
    --border: 217.2 32.6% 17.5%;
    --input: 217.2 32.6% 17.5%;
    --ring: 212.7 26.8% 83.9%;
  }

  * {
    @apply border-border;
  }
  body {
    @apply bg-background text-foreground;
  }
}
```

- [ ] **Step 4: Verify build still passes**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm build
```

Expected: `dist/` directory created, no errors.

- [ ] **Step 5: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/tailwind.config.ts frontend/postcss.config.js frontend/src/index.css
git commit -m "feat(frontend): add Tailwind CSS + shadcn theme variables"
```

---

## Task 4: Initialize shadcn/ui and add core components

**Files:**

- Create: `frontend/components.json`
- Create: `frontend/src/lib/cn.ts`
- Create: `frontend/src/components/ui/*.tsx` (via shadcn CLI)

- [ ] **Step 1: Create `components.json`**

Write `frontend/components.json`:

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "default",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "tailwind.config.ts",
    "css": "src/index.css",
    "baseColor": "slate",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/cn",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "iconLibrary": "lucide"
}
```

- [ ] **Step 2: Create `src/lib/cn.ts` utility**

Write `frontend/src/lib/cn.ts`:

```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 3: Add shadcn/ui primitives via CLI**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm dlx shadcn@latest add -y \
  button card input label textarea form select \
  dialog sheet drawer tabs alert badge skeleton \
  table tooltip toast toaster progress separator \
  dropdown-menu popover command scroll-area
```

Expected: files appear under `frontend/src/components/ui/`. The CLI will also create `src/hooks/use-toast.ts` (shadcn convention).

- [ ] **Step 4: Verify build**

```bash
pnpm typecheck && pnpm build
```

Expected: passes.

- [ ] **Step 5: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/components.json frontend/src/lib/cn.ts frontend/src/components/ui frontend/src/hooks
git commit -m "feat(frontend): init shadcn/ui + add core component primitives"
```

---

## Task 5: Configure Vitest + Playwright + ESLint

**Files:**

- Create: `frontend/vitest.config.ts`
- Create: `frontend/tests/setup.ts`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/eslint.config.js`

- [ ] **Step 1: Create `vitest.config.ts`**

Write `frontend/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    coverage: {
      reporter: ["text", "html"],
      include: ["src/lib/**", "src/hooks/**"],
    },
  },
});
```

- [ ] **Step 2: Create `tests/setup.ts`**

```ts
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());
```

- [ ] **Step 3: Create `playwright.config.ts`**

```ts
import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  expect: { timeout: 10_000 },
  fullyParallel: false, // tests share backend state; keep sequential
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
```

- [ ] **Step 4: Create `eslint.config.js`**

```js
import js from "@eslint/js";
import tseslint from "@typescript-eslint/eslint-plugin";
import tsparser from "@typescript-eslint/parser";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["dist", "node_modules", "src/api/schema.gen.ts"] },
  js.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsparser,
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { window: "readonly", document: "readonly", fetch: "readonly" },
    },
    plugins: {
      "@typescript-eslint": tseslint,
      react,
      "react-hooks": reactHooks,
    },
    rules: {
      ...tseslint.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      "react/react-in-jsx-scope": "off",
    },
    settings: { react: { version: "detect" } },
  },
];
```

- [ ] **Step 5: Sanity test — run vitest + playwright install**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm test   # vitest should report "No test files found" (exit 0 OK since no tests yet, or use --passWithNoTests)
pnpm exec playwright install chromium
```

Expected: vitest reports no tests (harmless); playwright downloads chromium (~180 MB one-time).

- [ ] **Step 6: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/vitest.config.ts frontend/tests/setup.ts frontend/playwright.config.ts frontend/eslint.config.js
git commit -m "feat(frontend): configure Vitest + Playwright + ESLint"
```

---

## Task 6: Backend — add FastAPI Users cookie auth transport

**Files:**

- Modify: `backend/app/config.py`
- Modify: `backend/app/users.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_auth_cookie.py`

- [ ] **Step 1: Write failing test for cookie login**

Write `backend/tests/test_auth_cookie.py`:

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_cookie_login_sets_httponly_cookie(async_client: AsyncClient, seed_user):
    email, password = seed_user
    resp = await async_client.post(
        "/api/v1/auth/cookie/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 204
    set_cookie = resp.headers.get("set-cookie", "")
    assert "lolday_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie.lower()
    assert "Path=/" in set_cookie


@pytest.mark.asyncio
async def test_cookie_login_rejects_bad_creds(async_client: AsyncClient, seed_user):
    email, _ = seed_user
    resp = await async_client.post(
        "/api/v1/auth/cookie/login",
        data={"username": email, "password": "wrong"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_cookie_logout_clears_cookie(async_client: AsyncClient, seed_user):
    email, password = seed_user
    await async_client.post(
        "/api/v1/auth/cookie/login",
        data={"username": email, "password": password},
    )
    resp = await async_client.post("/api/v1/auth/cookie/logout")
    assert resp.status_code == 204
    set_cookie = resp.headers.get("set-cookie", "")
    assert "Max-Age=0" in set_cookie or 'Max-Age="0"' in set_cookie


@pytest.mark.asyncio
async def test_bearer_login_still_works(async_client: AsyncClient, seed_user):
    """Regression: existing Bearer flow must keep working for Phase 4 curl E2E."""
    email, password = seed_user
    resp = await async_client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
```

- [ ] **Step 2: Run tests — they should fail**

```bash
cd /home/bolin8017/Documents/repositories/lolday/backend
uv run pytest tests/test_auth_cookie.py -v
```

Expected: 404 on `/api/v1/auth/cookie/login` (route doesn't exist yet) — first 3 tests FAIL, 4th PASS.

- [ ] **Step 3: Add settings to `config.py`**

Edit `backend/app/config.py`. Find the `Settings` class and add:

```python
    # Cookie auth (Phase 5)
    COOKIE_LIFETIME_SECONDS: int = 12 * 60 * 60   # 12 hours sliding
    COOKIE_SECURE: bool = True                     # set False in dev env
    COOKIE_NAME: str = "lolday_session"
    COOKIE_SAMESITE: str = "lax"
```

- [ ] **Step 4: Add CookieTransport + backend to `users.py`**

Edit `backend/app/users.py`. Add after the existing `bearer_transport` / `auth_backend` block:

```python
from fastapi_users.authentication import CookieTransport

cookie_transport = CookieTransport(
    cookie_name=settings.COOKIE_NAME,
    cookie_max_age=settings.COOKIE_LIFETIME_SECONDS,
    cookie_httponly=True,
    cookie_secure=settings.COOKIE_SECURE,
    cookie_samesite=settings.COOKIE_SAMESITE,  # type: ignore[arg-type]
    cookie_path="/",
    cookie_domain=None,
)

cookie_auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)
```

Then update the `fastapi_users` construction to register both backends:

```python
fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend, cookie_auth_backend],
)
```

- [ ] **Step 5: Mount cookie auth router in `main.py`**

Edit `backend/app/main.py`. After the existing `fastapi_users.get_auth_router(auth_backend)` mount, add:

```python
from app.users import cookie_auth_backend  # add to existing import if missing

app.include_router(
    fastapi_users.get_auth_router(cookie_auth_backend),
    prefix="/api/v1/auth/cookie",
    tags=["auth"],
)
```

- [ ] **Step 6: Run tests — should now pass**

```bash
cd /home/bolin8017/Documents/repositories/lolday/backend
uv run pytest tests/test_auth_cookie.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 7: Run full backend test suite — no regressions**

```bash
cd /home/bolin8017/Documents/repositories/lolday/backend
uv run pytest -x -q
```

Expected: all prior tests still pass.

- [ ] **Step 8: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add backend/app/config.py backend/app/users.py backend/app/main.py backend/tests/test_auth_cookie.py
git commit -m "feat(backend): add FastAPI Users CookieTransport for frontend auth"
```

---

## Task 7: Generate API types + build openapi-fetch client

**Files:**

- Create: `frontend/scripts/gen-api-types.sh`
- Create: `frontend/src/api/schema.gen.ts` (generated)
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/errors.ts`

- [ ] **Step 1: Start backend locally (background) to serve `openapi.json`**

```bash
# In a separate terminal / port-forward session — user prerequisite
kubectl -n lolday port-forward svc/backend 8000:8000 &
# or run backend locally:
# cd backend && uv run uvicorn app.main:app --port 8000
```

Verify:

```bash
curl -s http://localhost:8000/openapi.json | head -c 200
```

Expected: JSON starting with `{"openapi":"3.1.0",...}`.

- [ ] **Step 2: Create `scripts/gen-api-types.sh`**

Write `frontend/scripts/gen-api-types.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCHEMA_URL=${SCHEMA_URL:-http://localhost:8000/openapi.json}
OUT=src/api/schema.gen.ts

pnpm exec openapi-typescript "$SCHEMA_URL" -o "$OUT"
echo "Generated $OUT"
```

```bash
chmod +x frontend/scripts/gen-api-types.sh
```

- [ ] **Step 3: Run codegen**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm run gen-api-types
```

Expected: `src/api/schema.gen.ts` created (multi-thousand lines of types).

- [ ] **Step 4: Write `src/api/errors.ts`**

```ts
export interface ValidationFieldError {
  /** Dotted path into form, e.g., "body.email" → "email". */
  field: string;
  message: string;
}

export class LoldayApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly fieldErrors: ValidationFieldError[];

  constructor(
    status: number,
    detail: string,
    fieldErrors: ValidationFieldError[] = [],
  ) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
    this.fieldErrors = fieldErrors;
  }
}

type RawValidationItem = { loc: (string | number)[]; msg: string };

export function parseError(status: number, body: unknown): LoldayApiError {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (Array.isArray(detail)) {
      const fieldErrors: ValidationFieldError[] = detail
        .filter(
          (d): d is RawValidationItem =>
            typeof d === "object" && d !== null && "loc" in d && "msg" in d,
        )
        .map((d) => ({
          field: d.loc.filter((p) => p !== "body").join("."),
          message: d.msg,
        }));
      return new LoldayApiError(status, "Validation failed", fieldErrors);
    }
    if (typeof detail === "string") {
      return new LoldayApiError(status, detail);
    }
  }
  return new LoldayApiError(status, `HTTP ${status}`);
}
```

- [ ] **Step 5: Write `src/api/client.ts`**

```ts
import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema.gen";
import { parseError } from "./errors";

// The generated schema already prefixes operation paths with "/api/v1" (that's
// what the backend emits in openapi.json), so the baseUrl must be empty —
// otherwise every request would hit `/api/v1/api/v1/...` and 404.
const API_BASE = "";

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
      ? await response
          .clone()
          .json()
          .catch(() => null)
      : null;

    if (response.status === 401 && on401Handler) {
      on401Handler();
    }

    throw parseError(response.status, body);
  },
};

export const client = createClient<paths>({
  baseUrl: API_BASE,
  credentials: "include", // send cookies on every request
});

client.use(errorMiddleware);
```

- [ ] **Step 6: Sanity — typecheck**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm typecheck
```

Expected: passes.

- [ ] **Step 7: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/scripts/gen-api-types.sh frontend/src/api/
git commit -m "feat(frontend): generate API types + openapi-fetch client with error middleware"
```

---

## Task 8: Wire TanStack Query + React Router v7 providers

**Files:**

- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/api/queryClient.ts`

- [ ] **Step 1: Create `src/api/queryClient.ts`**

```ts
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error: unknown) => {
        // Don't retry 401/403/404
        if (typeof error === "object" && error !== null && "status" in error) {
          const status = (error as { status: number }).status;
          if ([401, 403, 404].includes(status)) return false;
        }
        return failureCount < 2;
      },
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});
```

- [ ] **Step 2: Replace `src/App.tsx` with provider wiring**

```tsx
import { useEffect } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createBrowserRouter, redirect } from "react-router";
import { queryClient } from "./api/queryClient";
import { setOn401 } from "./api/client";
import { Toaster } from "@/components/ui/toaster";

const router = createBrowserRouter([
  {
    path: "/",
    lazy: async () => ({
      Component: (await import("./routes/_authed")).default,
    }),
    children: [
      {
        index: true,
        loader: () => redirect("/detectors"),
      },
    ],
  },
  {
    path: "/login",
    lazy: async () => ({
      Component: (await import("./routes/_public.login")).default,
    }),
  },
]);

export default function App() {
  useEffect(() => {
    setOn401(() => {
      queryClient.clear();
      window.location.href = "/login";
    });
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>
  );
}
```

- [ ] **Step 3: Stub route files so lazy imports resolve**

Write `frontend/src/routes/_authed.tsx`:

```tsx
import { Outlet } from "react-router";

export default function AuthedLayout() {
  return (
    <div className="min-h-screen bg-background">
      <p className="p-8 text-muted-foreground">authed layout (stub)</p>
      <Outlet />
    </div>
  );
}
```

Write `frontend/src/routes/_public.login.tsx`:

```tsx
export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-muted-foreground">login (stub)</p>
    </div>
  );
}
```

- [ ] **Step 4: Start dev server and spot-check**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm dev
```

Open `http://localhost:5173/` → expect redirect to `/detectors` → "authed layout (stub)".

Open `http://localhost:5173/login` → expect "login (stub)".

Kill dev server (Ctrl+C).

- [ ] **Step 5: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/api/queryClient.ts frontend/src/App.tsx frontend/src/routes/
git commit -m "feat(frontend): wire QueryClient + React Router providers"
```

---

## Task 9: Set up i18n (react-i18next)

**Files:**

- Create: `frontend/src/i18n/index.ts`
- Create: `frontend/src/i18n/en.json`
- Create: `frontend/src/i18n/zh-TW.json`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Create `src/i18n/index.ts`**

```ts
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import en from "./en.json";
import zhTW from "./zh-TW.json";

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng: "en",
    supportedLngs: ["en", "zh-TW"],
    resources: {
      en: { translation: en },
      "zh-TW": { translation: zhTW },
    },
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
    },
  });

export default i18n;
```

- [ ] **Step 2: Create `src/i18n/en.json` with initial strings**

```json
{
  "app": { "name": "Lolday" },
  "nav": {
    "detectors": "Detectors",
    "datasets": "Datasets",
    "jobs": "Jobs",
    "runs": "Runs",
    "models": "Models",
    "profile": "Profile",
    "logout": "Log out"
  },
  "login": {
    "title": "Sign in to Lolday",
    "email": "Email",
    "password": "Password",
    "submit": "Sign in",
    "invalidCredentials": "Invalid email or password.",
    "rateLimited": "Too many attempts. Try again in a moment."
  },
  "common": {
    "cancel": "Cancel",
    "save": "Save",
    "delete": "Delete",
    "confirm": "Confirm",
    "loading": "Loading…",
    "noData": "No data.",
    "error": "Error",
    "retry": "Retry"
  },
  "status": {
    "pending": "Pending",
    "preparing": "Preparing",
    "running": "Running",
    "succeeded": "Succeeded",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "timeout": "Timed out",
    "scanning": "Scanning",
    "building": "Building",
    "success": "Success"
  }
}
```

- [ ] **Step 3: Create `src/i18n/zh-TW.json`**

```json
{}
```

(Intentionally empty — react-i18next falls back to `en`. Populate later.)

- [ ] **Step 4: Import i18n in `src/main.tsx`**

Edit `frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import "./i18n";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 5: Typecheck + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend && pnpm typecheck
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/i18n frontend/src/main.tsx
git commit -m "feat(frontend): configure react-i18next with en primary + zh-TW scaffold"
```

---

## Task 10: Lib utilities (date, status, errors) with tests

**Files:**

- Create: `frontend/src/lib/date.ts`
- Create: `frontend/src/lib/status.ts`
- Create: `frontend/src/lib/errors.ts`
- Create: `frontend/src/lib/csv.ts`
- Create: `frontend/tests/unit/lib/date.test.ts`
- Create: `frontend/tests/unit/lib/status.test.ts`
- Create: `frontend/tests/unit/lib/errors.test.ts`
- Create: `frontend/tests/unit/lib/csv.test.ts`

- [ ] **Step 1: Write failing test for `formatDuration`**

Write `frontend/tests/unit/lib/date.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { formatDuration, formatRelative } from "@/lib/date";

describe("formatDuration", () => {
  it("returns em-dash for null/undefined", () => {
    expect(formatDuration(null, null)).toBe("—");
    expect(formatDuration("2026-01-01T00:00:00Z", null)).toBe("—");
  });

  it("formats seconds under a minute", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:00:45Z")).toBe(
      "45s",
    );
  });

  it("formats minutes + seconds", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:02:03Z")).toBe(
      "2m 3s",
    );
  });

  it("formats hours + minutes", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T01:30:00Z")).toBe(
      "1h 30m",
    );
  });
});

describe("formatRelative", () => {
  it("handles recent", () => {
    const now = new Date();
    const tenSecAgo = new Date(now.getTime() - 10_000).toISOString();
    expect(formatRelative(tenSecAgo)).toMatch(/seconds ago/);
  });
});
```

- [ ] **Step 2: Run test — should fail (module not found)**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm test -- date.test
```

Expected: FAIL ("Cannot find module @/lib/date").

- [ ] **Step 3: Implement `src/lib/date.ts`**

```ts
import { formatDistanceToNow } from "date-fns";

export function formatDuration(
  start: string | null | undefined,
  end: string | null | undefined,
): string {
  if (!start || !end) return "—";
  const secs = Math.max(
    0,
    Math.floor((new Date(end).getTime() - new Date(start).getTime()) / 1000),
  );
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  return formatDistanceToNow(new Date(iso), { addSuffix: true });
}
```

- [ ] **Step 4: Run test — should pass**

```bash
pnpm test -- date.test
```

Expected: PASS.

- [ ] **Step 5: Write failing test for status color mapping**

Write `frontend/tests/unit/lib/status.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  statusTone,
  isTerminal,
  NON_TERMINAL_JOB_STATUSES,
} from "@/lib/status";

describe("statusTone", () => {
  it("maps success-ish statuses to success", () => {
    expect(statusTone("succeeded")).toBe("success");
    expect(statusTone("success")).toBe("success");
  });
  it("maps failed to destructive", () => {
    expect(statusTone("failed")).toBe("destructive");
    expect(statusTone("timeout")).toBe("destructive");
  });
  it("maps running to info", () => {
    expect(statusTone("running")).toBe("info");
    expect(statusTone("scanning")).toBe("info");
  });
  it("maps pending to muted", () => {
    expect(statusTone("pending")).toBe("muted");
  });
});

describe("isTerminal", () => {
  it("returns false for running-ish statuses", () => {
    for (const s of NON_TERMINAL_JOB_STATUSES)
      expect(isTerminal(s)).toBe(false);
  });
  it("returns true for succeeded / failed / cancelled / timeout", () => {
    expect(isTerminal("succeeded")).toBe(true);
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("cancelled")).toBe(true);
    expect(isTerminal("timeout")).toBe(true);
  });
});
```

- [ ] **Step 6: Implement `src/lib/status.ts`**

```ts
export const NON_TERMINAL_JOB_STATUSES = [
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
```

- [ ] **Step 7: Run test — PASS**

```bash
pnpm test -- status.test
```

- [ ] **Step 8: Write failing test + impl for errors helper**

Write `frontend/tests/unit/lib/errors.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { LoldayApiError } from "@/api/errors";
import { applyFieldErrorsToForm } from "@/lib/errors";

describe("applyFieldErrorsToForm", () => {
  it("calls setError for each field error", () => {
    const setError = vi.fn();
    const err = new LoldayApiError(422, "Validation failed", [
      { field: "email", message: "Not a valid email" },
      { field: "password", message: "Too short" },
    ]);
    applyFieldErrorsToForm(err, setError as any);
    expect(setError).toHaveBeenCalledTimes(2);
    expect(setError).toHaveBeenCalledWith("email", {
      type: "server",
      message: "Not a valid email",
    });
  });
});
```

Write `frontend/src/lib/errors.ts`:

```ts
import type { UseFormSetError, FieldValues, Path } from "react-hook-form";
import type { LoldayApiError } from "@/api/errors";

export function applyFieldErrorsToForm<T extends FieldValues>(
  err: LoldayApiError,
  setError: UseFormSetError<T>,
): void {
  for (const fe of err.fieldErrors) {
    setError(fe.field as Path<T>, { type: "server", message: fe.message });
  }
}
```

```bash
pnpm test -- errors.test
```

Expected: PASS.

- [ ] **Step 9: Write CSV preview parser + test**

Write `frontend/tests/unit/lib/csv.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { parseCsvPreview } from "@/lib/csv";

describe("parseCsvPreview", () => {
  it("parses header + rows", () => {
    const csv = "file_name,label\nabc,Malware\ndef,Benign\n";
    const p = parseCsvPreview(csv);
    expect(p.columns).toEqual(["file_name", "label"]);
    expect(p.rows.length).toBe(2);
    expect(p.rows[0]).toEqual({ file_name: "abc", label: "Malware" });
    expect(p.totalRows).toBe(2);
  });

  it("caps rows at limit", () => {
    const rows = Array.from({ length: 50 }, (_, i) => `f${i},Malware`).join(
      "\n",
    );
    const csv = `file_name,label\n${rows}\n`;
    const p = parseCsvPreview(csv, 20);
    expect(p.rows.length).toBe(20);
    expect(p.totalRows).toBe(50);
  });

  it("rejects missing required columns", () => {
    expect(() => parseCsvPreview("foo,bar\n1,2\n")).toThrow(/required/i);
  });
});
```

Write `frontend/src/lib/csv.ts`:

```ts
export interface CsvPreview {
  columns: string[];
  rows: Record<string, string>[];
  totalRows: number;
}

const REQUIRED = ["file_name", "label"];

export function parseCsvPreview(text: string, limit = 20): CsvPreview {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length === 0) throw new Error("Empty CSV");
  const columns = splitLine(lines[0]);
  for (const req of REQUIRED) {
    if (!columns.includes(req))
      throw new Error(`Missing required column: ${req}`);
  }
  const dataLines = lines.slice(1);
  const rows = dataLines.slice(0, limit).map((line) => {
    const cells = splitLine(line);
    return Object.fromEntries(columns.map((c, i) => [c, cells[i] ?? ""]));
  });
  return { columns, rows, totalRows: dataLines.length };
}

// RFC 4180 minimal — handles quoted fields with commas/quotes.
function splitLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuote) {
      if (ch === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') inQuote = false;
      else cur += ch;
    } else {
      if (ch === ",") {
        out.push(cur);
        cur = "";
      } else if (ch === '"') inQuote = true;
      else cur += ch;
    }
  }
  out.push(cur);
  return out;
}
```

```bash
pnpm test
```

Expected: all 4 suites PASS.

- [ ] **Step 10: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/lib frontend/tests/unit/lib
git commit -m "feat(frontend): lib utilities (date, status, errors, csv) with unit tests"
```

---

## Task 11: `usePolling` hook with test

**Files:**

- Create: `frontend/src/hooks/usePolling.ts`
- Create: `frontend/tests/unit/hooks/usePolling.test.ts`

- [ ] **Step 1: Write failing test**

Write `frontend/tests/unit/hooks/usePolling.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { computePollInterval } from "@/hooks/usePolling";

describe("computePollInterval", () => {
  it("returns interval when predicate says active", () => {
    expect(computePollInterval(true, 2000)).toBe(2000);
  });
  it("returns false when inactive", () => {
    expect(computePollInterval(false, 2000)).toBe(false);
  });
  it("handles undefined data safely", () => {
    expect(computePollInterval(undefined, 2000)).toBe(false);
  });
});
```

- [ ] **Step 2: Implement `src/hooks/usePolling.ts`**

```ts
/**
 * Compute a TanStack Query `refetchInterval` value given a predicate.
 *
 * Usage:
 *   useQuery({
 *     queryKey: [...],
 *     queryFn: ...,
 *     refetchInterval: (query) =>
 *       computePollInterval(isNonTerminal(query.state.data?.data?.status), 2000),
 *   })
 */
export function computePollInterval(
  isActive: boolean | undefined,
  intervalMs: number,
): number | false {
  return isActive ? intervalMs : false;
}
```

- [ ] **Step 3: Run test + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm test -- usePolling
```

Expected: PASS.

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/hooks/usePolling.ts frontend/tests/unit/hooks/usePolling.test.ts
git commit -m "feat(frontend): add usePolling helper with unit test"
```

---

## Task 12: Sidebar layout component

**Files:**

- Create: `frontend/src/components/layout/Sidebar.tsx`

- [ ] **Step 1: Create Sidebar**

Write `frontend/src/components/layout/Sidebar.tsx`:

```tsx
import { NavLink } from "react-router";
import { useTranslation } from "react-i18next";
import {
  Package,
  FolderOpen,
  Play,
  BarChart3,
  Tag,
  User as UserIcon,
  LogOut,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/hooks/useAuth";

const NAV_ITEMS = [
  { to: "/detectors", icon: Package, labelKey: "nav.detectors" },
  { to: "/datasets", icon: FolderOpen, labelKey: "nav.datasets" },
  { to: "/jobs", icon: Play, labelKey: "nav.jobs" },
  { to: "/runs", icon: BarChart3, labelKey: "nav.runs" },
  { to: "/models", icon: Tag, labelKey: "nav.models" },
] as const;

export function Sidebar() {
  const { t } = useTranslation();
  const { currentUser, logout } = useAuth();
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r bg-slate-900 text-slate-100">
      <div className="px-5 py-5 text-lg font-semibold text-amber-400">
        {t("app.name")}
      </div>
      <Separator className="bg-slate-800" />
      <nav className="flex-1 space-y-1 px-3 py-4">
        {NAV_ITEMS.map(({ to, icon: Icon, labelKey }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-slate-800 text-white"
                  : "text-slate-300 hover:bg-slate-800/60 hover:text-white",
              )
            }
          >
            <Icon className="h-4 w-4" />
            {t(labelKey)}
          </NavLink>
        ))}
      </nav>
      <Separator className="bg-slate-800" />
      <div className="px-3 py-4 space-y-2">
        <NavLink
          to="/profile"
          className={({ isActive }) =>
            cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm",
              isActive
                ? "bg-slate-800 text-white"
                : "text-slate-300 hover:bg-slate-800/60",
            )
          }
        >
          <UserIcon className="h-4 w-4" />
          <span className="truncate">{currentUser?.email ?? "—"}</span>
        </NavLink>
        <Button
          variant="ghost"
          className="w-full justify-start text-slate-300 hover:bg-slate-800/60 hover:text-white"
          onClick={() => logout()}
        >
          <LogOut className="mr-3 h-4 w-4" />
          {t("nav.logout")}
        </Button>
        <p className="pt-2 text-[10px] text-slate-500">
          v{import.meta.env.VITE_APP_VERSION}
        </p>
      </div>
    </aside>
  );
}
```

- [ ] **Step 2: Commit (will wire up with useAuth in Task 14)**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/layout/Sidebar.tsx
git commit -m "feat(frontend): sidebar layout component"
```

---

## Task 13: TopBar + Breadcrumb

**Files:**

- Create: `frontend/src/components/layout/TopBar.tsx`
- Create: `frontend/src/components/layout/Breadcrumb.tsx`
- Create: `frontend/src/hooks/useBreadcrumb.ts`

- [ ] **Step 1: Create breadcrumb hook**

Write `frontend/src/hooks/useBreadcrumb.ts`:

```ts
import { useMatches } from "react-router";

export interface CrumbMatch {
  pathname: string;
  label: string;
}

/**
 * Routes can export a `handle.breadcrumb: (data) => string` (or a plain string).
 * `useBreadcrumb` collects the breadcrumb from every match that provides one.
 */
export function useBreadcrumb(): CrumbMatch[] {
  const matches = useMatches();
  return matches
    .filter(
      (
        m,
      ): m is typeof m & {
        handle: { breadcrumb: string | ((d: unknown) => string) };
      } =>
        Boolean(
          m.handle &&
          typeof m.handle === "object" &&
          m.handle !== null &&
          "breadcrumb" in m.handle,
        ),
    )
    .map((m) => {
      const b = (m.handle as { breadcrumb: string | ((d: unknown) => string) })
        .breadcrumb;
      return {
        pathname: m.pathname,
        label: typeof b === "function" ? b(m.data) : b,
      };
    });
}
```

- [ ] **Step 2: Create Breadcrumb component**

Write `frontend/src/components/layout/Breadcrumb.tsx`:

```tsx
import { Link } from "react-router";
import { ChevronRight } from "lucide-react";
import { useBreadcrumb } from "@/hooks/useBreadcrumb";

export function Breadcrumb() {
  const crumbs = useBreadcrumb();
  if (crumbs.length === 0) return null;
  return (
    <nav className="flex items-center text-sm text-muted-foreground">
      {crumbs.map((c, i) => (
        <span key={c.pathname} className="flex items-center">
          {i > 0 && <ChevronRight className="mx-2 h-3.5 w-3.5" />}
          {i === crumbs.length - 1 ? (
            <span className="text-foreground">{c.label}</span>
          ) : (
            <Link to={c.pathname} className="hover:text-foreground">
              {c.label}
            </Link>
          )}
        </span>
      ))}
    </nav>
  );
}
```

- [ ] **Step 3: Create TopBar**

Write `frontend/src/components/layout/TopBar.tsx`:

```tsx
import { Breadcrumb } from "./Breadcrumb";

export function TopBar() {
  return (
    <header className="flex h-14 items-center border-b bg-card px-6">
      <Breadcrumb />
    </header>
  );
}
```

- [ ] **Step 4: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/layout/TopBar.tsx frontend/src/components/layout/Breadcrumb.tsx frontend/src/hooks/useBreadcrumb.ts
git commit -m "feat(frontend): top bar + breadcrumb driven by route handles"
```

---

## Task 14: `useAuth` hook + route guard in authed layout

**Files:**

- Create: `frontend/src/hooks/useAuth.ts`
- Create: `frontend/src/api/queries/auth.ts`
- Modify: `frontend/src/routes/_authed.tsx`

- [ ] **Step 1: Create auth queries**

Write `frontend/src/api/queries/auth.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type User = components["schemas"]["UserRead"];

export const authKeys = {
  me: ["auth", "me"] as const,
};

export function useCurrentUser() {
  return useQuery({
    queryKey: authKeys.me,
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/users/me");
      if (error) throw error;
      return data as User;
    },
    retry: false,
    staleTime: 5 * 60_000,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { email: string; password: string }) => {
      // FastAPI Users login expects application/x-www-form-urlencoded with username/password
      const body = new URLSearchParams();
      body.set("username", args.email);
      body.set("password", args.password);
      const resp = await fetch(
        `${import.meta.env.VITE_API_BASE}/auth/cookie/login`,
        { method: "POST", body, credentials: "include" },
      );
      if (!resp.ok) {
        const detail = await resp
          .json()
          .catch(() => ({ detail: `HTTP ${resp.status}` }));
        throw Object.assign(new Error(detail.detail ?? "Login failed"), {
          status: resp.status,
        });
      }
    },
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await fetch(`${import.meta.env.VITE_API_BASE}/auth/cookie/logout`, {
        method: "POST",
        credentials: "include",
      });
    },
    onSettled: () => {
      qc.clear();
      window.location.href = "/login";
    },
  });
}
```

- [ ] **Step 2: Create `useAuth` hook**

Write `frontend/src/hooks/useAuth.ts`:

```ts
import { useCurrentUser, useLogout } from "@/api/queries/auth";

export function useAuth() {
  const userQuery = useCurrentUser();
  const logoutMut = useLogout();
  return {
    currentUser: userQuery.data ?? null,
    isLoading: userQuery.isLoading,
    isUnauthenticated:
      userQuery.isError &&
      (userQuery.error as { status?: number } | undefined)?.status === 401,
    logout: () => logoutMut.mutate(),
  };
}
```

- [ ] **Step 3: Wire route guard into `_authed.tsx`**

Write `frontend/src/routes/_authed.tsx`:

```tsx
import { Navigate, Outlet } from "react-router";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { useAuth } from "@/hooks/useAuth";

export default function AuthedLayout() {
  const { currentUser, isLoading, isUnauthenticated } = useAuth();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        Loading…
      </div>
    );
  }
  if (isUnauthenticated || !currentUser) {
    return <Navigate to="/login" replace />;
  }
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex flex-1 flex-col">
        <TopBar />
        <main className="flex-1 overflow-auto bg-background p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Smoke test via dev server**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm dev &
sleep 2
# With backend NOT running, should redirect to /login
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/
kill %1
```

Expected: initial page renders, then client-side redirects to `/login` because `/api/v1/users/me` errors.

- [ ] **Step 5: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/hooks/useAuth.ts frontend/src/api/queries/auth.ts frontend/src/routes/_authed.tsx
git commit -m "feat(frontend): useAuth hook + route guard on authed layout"
```

---

## Task 15: Login form + /login route

**Files:**

- Create: `frontend/src/components/forms/LoginForm.tsx`
- Modify: `frontend/src/routes/_public.login.tsx`
- Modify: `frontend/src/routes/_public.tsx`

- [ ] **Step 1: Create `_public.tsx` layout**

Write `frontend/src/routes/_public.tsx`:

```tsx
import { Outlet } from "react-router";

export default function PublicLayout() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-muted">
      <Outlet />
    </div>
  );
}
```

- [ ] **Step 2: Build `LoginForm.tsx`**

Write `frontend/src/components/forms/LoginForm.tsx`:

```tsx
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useLogin } from "@/api/queries/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const schema = z.object({
  email: z.string().email(),
  password: z.string().min(1, "Password is required"),
});
type FormValues = z.infer<typeof schema>;

export function LoginForm() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const login = useLogin();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
  });

  const onSubmit = handleSubmit(async (values) => {
    try {
      await login.mutateAsync(values);
      navigate("/", { replace: true });
    } catch {
      // error surfaced via login.isError below
    }
  });

  const serverError = login.isError
    ? (login.error as { status?: number }).status === 429
      ? t("login.rateLimited")
      : t("login.invalidCredentials")
    : null;

  return (
    <Card className="w-[380px]">
      <CardHeader>
        <CardTitle>{t("login.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={onSubmit}>
          <div className="space-y-2">
            <Label htmlFor="email">{t("login.email")}</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              {...register("email")}
            />
            {errors.email && (
              <p className="text-xs text-destructive">{errors.email.message}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">{t("login.password")}</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              {...register("password")}
            />
            {errors.password && (
              <p className="text-xs text-destructive">
                {errors.password.message}
              </p>
            )}
          </div>
          {serverError && (
            <Alert variant="destructive">
              <AlertDescription>{serverError}</AlertDescription>
            </Alert>
          )}
          <Button
            type="submit"
            className="w-full"
            disabled={isSubmitting || login.isPending}
          >
            {t("login.submit")}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Wire route**

Write `frontend/src/routes/_public.login.tsx`:

```tsx
import { LoginForm } from "@/components/forms/LoginForm";
export default function LoginPage() {
  return <LoginForm />;
}
```

- [ ] **Step 4: Update router to use `_public` layout**

Edit `frontend/src/App.tsx` — replace the `/login` route with a nested version:

```tsx
const router = createBrowserRouter([
  {
    path: "/",
    lazy: async () => ({
      Component: (await import("./routes/_authed")).default,
    }),
    children: [{ index: true, loader: () => redirect("/detectors") }],
  },
  {
    path: "/",
    lazy: async () => ({
      Component: (await import("./routes/_public")).default,
    }),
    children: [
      {
        path: "login",
        lazy: async () => ({
          Component: (await import("./routes/_public.login")).default,
        }),
      },
    ],
  },
]);
```

- [ ] **Step 5: Manual smoke test**

With backend port-forwarded (`kubectl -n lolday port-forward svc/backend 8000:8000`):

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm dev
```

Visit `http://localhost:5173/login`, fill admin creds from `~/.lolday-secrets.env`, submit → should redirect to `/detectors` (the authed layout will still show empty because `/detectors` route not built yet; but no 401 redirect back to login = auth works).

- [ ] **Step 6: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/forms/LoginForm.tsx frontend/src/routes/ frontend/src/App.tsx
git commit -m "feat(frontend): login form with cookie-based authentication"
```

---

## Task 16: Playwright helper + first E2E (login.spec.ts)

**Files:**

- Create: `frontend/tests/e2e/helpers.ts`
- Create: `frontend/tests/e2e/login.spec.ts`

- [ ] **Step 1: Create helpers**

Write `frontend/tests/e2e/helpers.ts`:

```ts
import type { Page } from "@playwright/test";

export interface SeedCreds {
  email: string;
  password: string;
}

/**
 * Credentials pulled from env — set E2E_ADMIN_EMAIL and E2E_ADMIN_PASSWORD
 * (usually same as ~/.lolday-secrets.env ADMIN_EMAIL/ADMIN_PASSWORD).
 */
export function seedCreds(): SeedCreds {
  const email = process.env.E2E_ADMIN_EMAIL;
  const password = process.env.E2E_ADMIN_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "Set E2E_ADMIN_EMAIL and E2E_ADMIN_PASSWORD before running E2E.",
    );
  }
  return { email, password };
}

export async function login(page: Page, creds: SeedCreds = seedCreds()) {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill(creds.email);
  await page.getByLabel(/password/i).fill(creds.password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/(detectors|)$/);
}
```

- [ ] **Step 2: Create `login.spec.ts`**

Write `frontend/tests/e2e/login.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { login, seedCreds } from "./helpers";

test("unauthenticated root redirects to /login", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL("**/login");
  await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();
});

test("valid creds reach the authed app", async ({ page }) => {
  await login(page);
  await expect(page).toHaveURL(/\/detectors/);
});

test("invalid creds show error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel(/email/i).fill(seedCreds().email);
  await page.getByLabel(/password/i).fill("definitely-wrong");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByText(/invalid email or password/i)).toBeVisible();
});
```

- [ ] **Step 3: Document how to run**

Append to `frontend/README.md` (create if missing):

````md
## E2E

Requires the backend to be reachable on `http://localhost:8000` (`kubectl port-forward svc/backend 8000:8000`) and credentials in env:

```bash
source ~/.lolday-secrets.env
export E2E_ADMIN_EMAIL=$ADMIN_EMAIL E2E_ADMIN_PASSWORD=$ADMIN_PASSWORD
pnpm dev &
pnpm test:e2e
```
````

````

- [ ] **Step 4: Run the spec**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
# Shell A: kubectl -n lolday port-forward svc/backend 8000:8000
# Shell B: pnpm dev
# Shell C:
source ~/.lolday-secrets.env
export E2E_ADMIN_EMAIL=$ADMIN_EMAIL E2E_ADMIN_PASSWORD=$ADMIN_PASSWORD
pnpm test:e2e login.spec
````

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/tests/e2e/ frontend/README.md
git commit -m "test(frontend): Playwright helpers + login.spec E2E"
```

---

## Task 17: Users / credentials query hooks

**Files:**

- Create: `frontend/src/api/queries/users.ts`

- [ ] **Step 1: Write the query hooks**

Write `frontend/src/api/queries/users.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import { authKeys } from "./auth";
import type { components } from "@/api/schema.gen";

export type GitCredential = components["schemas"]["GitCredentialRead"];

export const usersKeys = {
  gitCredential: ["users", "git-credential"] as const,
};

export function useGitCredential() {
  return useQuery({
    queryKey: usersKeys.gitCredential,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/users/me/git-credential",
      );
      if (response.status === 404) return null; // not set
      if (error) throw error;
      return data as GitCredential;
    },
    retry: false,
  });
}

export function useSetGitCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { provider: "github"; token: string }) => {
      const { data, error } = await client.PUT(
        "/api/v1/users/me/git-credential",
        { body: args },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: usersKeys.gitCredential }),
  });
}

export function useDeleteGitCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { error } = await client.DELETE("/api/v1/users/me/git-credential");
      if (error) throw error;
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: usersKeys.gitCredential }),
  });
}

export function useUpdatePassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { password: string }) => {
      const { data, error } = await client.PATCH("/api/v1/users/me", {
        body: args,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: authKeys.me }),
  });
}
```

- [ ] **Step 2: Typecheck + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend && pnpm typecheck
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/api/queries/users.ts
git commit -m "feat(frontend): user profile + git credential query hooks"
```

---

## Task 18: Profile page (password + git credential)

**Files:**

- Create: `frontend/src/components/forms/PasswordChangeForm.tsx`
- Create: `frontend/src/components/forms/GitCredentialForm.tsx`
- Create: `frontend/src/routes/_authed.profile.tsx`

- [ ] **Step 1: PasswordChangeForm**

Write `frontend/src/components/forms/PasswordChangeForm.tsx`:

```tsx
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useUpdatePassword } from "@/api/queries/users";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";

const schema = z
  .object({
    password: z.string().min(8, "At least 8 characters"),
    confirm: z.string(),
  })
  .refine((d) => d.password === d.confirm, {
    path: ["confirm"],
    message: "Passwords do not match",
  });
type Values = z.infer<typeof schema>;

export function PasswordChangeForm() {
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
  });
  const mut = useUpdatePassword();
  const { toast } = useToast();
  const onSubmit = handleSubmit(async (v) => {
    await mut.mutateAsync({ password: v.password });
    reset();
    toast({ title: "Password updated." });
  });
  return (
    <form className="space-y-3" onSubmit={onSubmit}>
      <div>
        <Label htmlFor="pw">New password</Label>
        <Input id="pw" type="password" {...register("password")} />
        {errors.password && (
          <p className="text-xs text-destructive">{errors.password.message}</p>
        )}
      </div>
      <div>
        <Label htmlFor="pw2">Confirm password</Label>
        <Input id="pw2" type="password" {...register("confirm")} />
        {errors.confirm && (
          <p className="text-xs text-destructive">{errors.confirm.message}</p>
        )}
      </div>
      <Button type="submit" disabled={isSubmitting}>
        Update password
      </Button>
    </form>
  );
}
```

- [ ] **Step 2: GitCredentialForm**

Write `frontend/src/components/forms/GitCredentialForm.tsx`:

```tsx
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  useGitCredential,
  useSetGitCredential,
  useDeleteGitCredential,
} from "@/api/queries/users";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useToast } from "@/hooks/use-toast";

const schema = z.object({
  token: z.string().min(20, "Looks too short for a PAT"),
});
type Values = z.infer<typeof schema>;

export function GitCredentialForm() {
  const { data: cred, isLoading } = useGitCredential();
  const setCred = useSetGitCredential();
  const clearCred = useDeleteGitCredential();
  const [editing, setEditing] = useState(false);
  const { toast } = useToast();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
  });

  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;

  if (cred && !editing) {
    return (
      <div className="space-y-3">
        <Alert>
          <AlertDescription>
            GitHub PAT is set (masked). Needed for detector builds.
          </AlertDescription>
        </Alert>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setEditing(true)}>
            Update
          </Button>
          <Button
            variant="destructive"
            onClick={async () => {
              await clearCred.mutateAsync();
              toast({ title: "Credential cleared." });
            }}
          >
            Clear
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form
      className="space-y-3"
      onSubmit={handleSubmit(async (v) => {
        await setCred.mutateAsync({ provider: "github", token: v.token });
        reset();
        setEditing(false);
        toast({ title: "GitHub PAT saved." });
      })}
    >
      <div>
        <Label htmlFor="tok">GitHub PAT</Label>
        <Input
          id="tok"
          type="password"
          autoComplete="off"
          {...register("token")}
        />
        {errors.token && (
          <p className="text-xs text-destructive">{errors.token.message}</p>
        )}
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={isSubmitting}>
          Save
        </Button>
        {editing && (
          <Button
            type="button"
            variant="ghost"
            onClick={() => setEditing(false)}
          >
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}
```

- [ ] **Step 3: Profile route**

Write `frontend/src/routes/_authed.profile.tsx`:

```tsx
import { useAuth } from "@/hooks/useAuth";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PasswordChangeForm } from "@/components/forms/PasswordChangeForm";
import { GitCredentialForm } from "@/components/forms/GitCredentialForm";

export const handle = { breadcrumb: "Profile" };

export default function ProfilePage() {
  const { currentUser } = useAuth();
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div>
            <span className="text-muted-foreground">Email:</span>{" "}
            {currentUser?.email}
          </div>
          <div>
            <span className="text-muted-foreground">Role:</span>{" "}
            {currentUser?.role ?? "user"}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Change password</CardTitle>
        </CardHeader>
        <CardContent>
          <PasswordChangeForm />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>GitHub PAT</CardTitle>
        </CardHeader>
        <CardContent>
          <GitCredentialForm />
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Add `/profile` to router**

Edit `frontend/src/App.tsx` — add under the authed layout's `children`:

```tsx
{
  path: "profile",
  lazy: async () => ({
    Component: (await import("./routes/_authed.profile")).default,
    handle: (await import("./routes/_authed.profile")).handle,
  }),
},
```

- [ ] **Step 5: Manual smoke test**

Dev server + backend port-forward → login → navigate to `/profile` via sidebar → set a PAT → clear → change password.

- [ ] **Step 6: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/forms/PasswordChangeForm.tsx frontend/src/components/forms/GitCredentialForm.tsx frontend/src/routes/_authed.profile.tsx frontend/src/App.tsx
git commit -m "feat(frontend): profile page (password change + git credential)"
```

---

## Task 19: Reusable DataTable + StatusBadge + Detectors query hooks

**Files:**

- Create: `frontend/src/components/tables/DataTable.tsx`
- Create: `frontend/src/components/common/StatusBadge.tsx`
- Create: `frontend/src/api/queries/detectors.ts`

- [ ] **Step 1: Build DataTable wrapper around TanStack Table**

Write `frontend/src/components/tables/DataTable.tsx`:

```tsx
import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { ArrowUpDown } from "lucide-react";

interface Props<T> {
  data: T[];
  columns: ColumnDef<T>[];
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
}

export function DataTable<T>({
  data,
  columns,
  emptyMessage = "No data.",
  onRowClick,
}: Props<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });
  return (
    <div className="space-y-3">
      <div className="overflow-hidden rounded-md border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id}>
                {hg.headers.map((h) => (
                  <TableHead key={h.id}>
                    {h.isPlaceholder ? null : h.column.getCanSort() ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={h.column.getToggleSortingHandler()}
                      >
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        <ArrowUpDown className="ml-2 h-3 w-3" />
                      </Button>
                    ) : (
                      flexRender(h.column.columnDef.header, h.getContext())
                    )}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-24 text-center text-muted-foreground"
                >
                  {emptyMessage}
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  onClick={
                    onRowClick ? () => onRowClick(row.original) : undefined
                  }
                  className={onRowClick ? "cursor-pointer" : undefined}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext(),
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => table.previousPage()}
          disabled={!table.getCanPreviousPage()}
        >
          Prev
        </Button>
        <span className="text-sm text-muted-foreground">
          Page {table.getState().pagination.pageIndex + 1} of{" "}
          {table.getPageCount() || 1}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => table.nextPage()}
          disabled={!table.getCanNextPage()}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: StatusBadge**

Write `frontend/src/components/common/StatusBadge.tsx`:

```tsx
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { statusTone, type Tone } from "@/lib/status";
import { useTranslation } from "react-i18next";

const TONE_CLASSES: Record<Tone, string> = {
  success: "bg-emerald-100 text-emerald-700 hover:bg-emerald-100",
  destructive: "bg-red-100 text-red-700 hover:bg-red-100",
  info: "bg-sky-100 text-sky-700 hover:bg-sky-100",
  muted: "bg-slate-100 text-slate-700 hover:bg-slate-100",
  warning: "bg-amber-100 text-amber-700 hover:bg-amber-100",
};

export function StatusBadge({ status }: { status: string }) {
  const { t, i18n } = useTranslation();
  const key = `status.${status}`;
  const label = i18n.exists(key) ? t(key) : status;
  return (
    <Badge className={cn(TONE_CLASSES[statusTone(status)])}>{label}</Badge>
  );
}
```

- [ ] **Step 3: Detectors queries**

Write `frontend/src/api/queries/detectors.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type Detector = components["schemas"]["DetectorRead"];
export type DetectorVersion = components["schemas"]["VersionDetailRead"];
export type Build = components["schemas"]["BuildRead"];

export const detectorsKeys = {
  all: ["detectors"] as const,
  list: () => [...detectorsKeys.all, "list"] as const,
  detail: (id: string) => [...detectorsKeys.all, "detail", id] as const,
  versions: (id: string) => [...detectorsKeys.all, "versions", id] as const,
  version: (id: string, tag: string) =>
    [...detectorsKeys.all, "version", id, tag] as const,
  builds: (id: string) => [...detectorsKeys.all, "builds", id] as const,
  build: (id: string, bid: string) =>
    [...detectorsKeys.all, "build", id, bid] as const,
  availableTags: (id: string) =>
    [...detectorsKeys.all, "available-tags", id] as const,
};

export function useDetectors() {
  return useQuery({
    queryKey: detectorsKeys.list(),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/detectors");
      if (error) throw error;
      return data;
    },
  });
}

export function useDetector(id: string) {
  return useQuery({
    queryKey: detectorsKeys.detail(id),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/detectors/{detector_id}",
        {
          params: { path: { detector_id: id } },
        },
      );
      if (error) throw error;
      return data as Detector;
    },
  });
}

export function useDetectorVersions(id: string) {
  return useQuery({
    queryKey: detectorsKeys.versions(id),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/detectors/{detector_id}/versions",
        {
          params: { path: { detector_id: id } },
        },
      );
      if (error) throw error;
      return data;
    },
  });
}

export function useDetectorVersion(id: string, tag: string) {
  return useQuery({
    queryKey: detectorsKeys.version(id, tag),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/detectors/{detector_id}/versions/{tag}",
        {
          params: { path: { detector_id: id, tag } },
        },
      );
      if (error) throw error;
      return data as DetectorVersion;
    },
    enabled: Boolean(id && tag),
  });
}

export function useDetectorBuilds(id: string) {
  return useQuery({
    queryKey: detectorsKeys.builds(id),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/detectors/{detector_id}/builds",
        {
          params: { path: { detector_id: id } },
        },
      );
      if (error) throw error;
      return data;
    },
    refetchInterval: (q) => {
      const builds = (q.state.data as { data?: Build[] } | undefined)?.data;
      if (!builds) return false;
      const anyActive = builds.some((b) =>
        ["pending", "building", "scanning"].includes(b.status),
      );
      return anyActive ? 2000 : false;
    },
  });
}

export function useAvailableTags(id: string) {
  return useQuery({
    queryKey: detectorsKeys.availableTags(id),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/detectors/{detector_id}/available-tags",
        {
          params: { path: { detector_id: id } },
        },
      );
      if (error) throw error;
      return data as { tag: string; sha: string }[];
    },
    enabled: Boolean(id),
  });
}

export function useRegisterDetector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["DetectorCreate"]) => {
      const { data, error } = await client.POST("/api/v1/detectors", { body });
      if (error) throw error;
      return data as Detector;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: detectorsKeys.all }),
  });
}

export function useTriggerBuild(detectorId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { git_tag: string }) => {
      const { data, error } = await client.POST(
        "/api/v1/detectors/{detector_id}/builds",
        {
          params: { path: { detector_id: detectorId } },
          body,
        },
      );
      if (error) throw error;
      return data as Build;
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: detectorsKeys.builds(detectorId) }),
  });
}

export function useCancelBuild(detectorId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (buildId: string) => {
      const { data, error } = await client.POST(
        "/api/v1/detectors/{detector_id}/builds/{build_id}/cancel",
        { params: { path: { detector_id: detectorId, build_id: buildId } } },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: detectorsKeys.builds(detectorId) }),
  });
}

export function useDeleteDetector() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await client.DELETE("/api/v1/detectors/{detector_id}", {
        params: { path: { detector_id: id } },
      });
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: detectorsKeys.all }),
  });
}
```

- [ ] **Step 4: Typecheck + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend && pnpm typecheck
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/tables/DataTable.tsx frontend/src/components/common/StatusBadge.tsx frontend/src/api/queries/detectors.ts
git commit -m "feat(frontend): DataTable + StatusBadge + detectors query hooks"
```

---

## Task 20: Detectors list + register

**Files:**

- Create: `frontend/src/routes/_authed.detectors._index.tsx`
- Create: `frontend/src/routes/_authed.detectors.new.tsx`
- Create: `frontend/src/components/forms/RegisterDetectorForm.tsx`

- [ ] **Step 1: RegisterDetectorForm**

Write `frontend/src/components/forms/RegisterDetectorForm.tsx`:

```tsx
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate } from "react-router";
import { useRegisterDetector } from "@/api/queries/detectors";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { applyFieldErrorsToForm } from "@/lib/errors";
import type { LoldayApiError } from "@/api/errors";

const schema = z.object({
  name: z
    .string()
    .min(1)
    .regex(/^[a-z0-9-]+$/, "lowercase letters, digits, hyphen only"),
  display_name: z.string().min(1).max(200),
  description: z.string().optional(),
  git_url: z.string().url(),
});
type Values = z.infer<typeof schema>;

export function RegisterDetectorForm() {
  const nav = useNavigate();
  const mut = useRegisterDetector();
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
  });
  const onSubmit = handleSubmit(async (v) => {
    try {
      const det = await mut.mutateAsync(v);
      nav(`/detectors/${det.id}`);
    } catch (e) {
      applyFieldErrorsToForm(e as LoldayApiError, setError);
    }
  });
  return (
    <form className="space-y-4 max-w-xl" onSubmit={onSubmit}>
      <div>
        <Label htmlFor="name">Name (slug)</Label>
        <Input id="name" placeholder="upxelfdet" {...register("name")} />
        {errors.name && (
          <p className="text-xs text-destructive">{errors.name.message}</p>
        )}
      </div>
      <div>
        <Label htmlFor="display_name">Display name</Label>
        <Input
          id="display_name"
          placeholder="UPX ELF Detector"
          {...register("display_name")}
        />
        {errors.display_name && (
          <p className="text-xs text-destructive">
            {errors.display_name.message}
          </p>
        )}
      </div>
      <div>
        <Label htmlFor="git_url">Git URL</Label>
        <Input
          id="git_url"
          placeholder="https://github.com/…"
          {...register("git_url")}
        />
        {errors.git_url && (
          <p className="text-xs text-destructive">{errors.git_url.message}</p>
        )}
      </div>
      <div>
        <Label htmlFor="description">Description</Label>
        <Textarea id="description" rows={3} {...register("description")} />
      </div>
      <Button type="submit" disabled={isSubmitting}>
        Register detector
      </Button>
    </form>
  );
}
```

- [ ] **Step 2: Detectors list route**

Write `frontend/src/routes/_authed.detectors._index.tsx`:

```tsx
import { Link } from "react-router";
import { useDetectors, type Detector } from "@/api/queries/detectors";
import { DataTable } from "@/components/tables/DataTable";
import { Button } from "@/components/ui/button";
import { formatRelative } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";

export const handle = { breadcrumb: "Detectors" };

const columns: ColumnDef<Detector>[] = [
  { accessorKey: "display_name", header: "Name" },
  {
    accessorKey: "description",
    header: "Description",
    cell: ({ row }) => (
      <span className="text-muted-foreground">
        {row.original.description ?? "—"}
      </span>
    ),
  },
  {
    accessorKey: "git_url",
    header: "Git URL",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.git_url}</span>
    ),
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => formatRelative(row.original.created_at),
  },
];

export default function DetectorsListPage() {
  const { data, isLoading } = useDetectors();
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Detectors</h1>
        <Button asChild>
          <Link to="/detectors/new">
            <Plus className="mr-2 h-4 w-4" />
            Register
          </Link>
        </Button>
      </div>
      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : (
        <DataTable
          data={(data as Detector[]) ?? []}
          columns={columns}
          emptyMessage="No detectors registered yet."
          onRowClick={(d) => {
            window.location.href = `/detectors/${d.id}`;
          }}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 3: New detector route**

Write `frontend/src/routes/_authed.detectors.new.tsx`:

```tsx
import { RegisterDetectorForm } from "@/components/forms/RegisterDetectorForm";

export const handle = { breadcrumb: "New detector" };

export default function NewDetectorPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Register detector</h1>
      <RegisterDetectorForm />
    </div>
  );
}
```

- [ ] **Step 4: Wire routes in `App.tsx`**

Add to authed `children`:

```tsx
{
  path: "detectors",
  children: [
    { index: true, lazy: async () => ({
      Component: (await import("./routes/_authed.detectors._index")).default,
      handle: (await import("./routes/_authed.detectors._index")).handle,
    })},
    { path: "new", lazy: async () => ({
      Component: (await import("./routes/_authed.detectors.new")).default,
      handle: (await import("./routes/_authed.detectors.new")).handle,
    })},
  ],
},
```

- [ ] **Step 5: Smoke test + commit**

Dev server + backend → `/detectors` lists empty → `/detectors/new` → register upxelfdet → back to list → sees row.

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/routes/_authed.detectors._index.tsx frontend/src/routes/_authed.detectors.new.tsx frontend/src/components/forms/RegisterDetectorForm.tsx frontend/src/App.tsx
git commit -m "feat(frontend): detectors list + register detector form"
```

---

## Task 21: Detector detail (Overview / Versions / Builds tabs)

**Files:**

- Create: `frontend/src/routes/_authed.detectors.$id.tsx`
- Create: `frontend/src/components/common/JsonViewer.tsx`
- Create: `frontend/src/components/common/LogTail.tsx`

- [ ] **Step 1: JsonViewer (shared, used by detector versions + run params)**

Write `frontend/src/components/common/JsonViewer.tsx`:

```tsx
export function JsonViewer({ value }: { value: unknown }) {
  return (
    <pre className="overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
```

- [ ] **Step 2: LogTail (auto-scrolling pre)**

Write `frontend/src/components/common/LogTail.tsx`:

```tsx
import { useEffect, useRef } from "react";
import { cn } from "@/lib/cn";

interface Props {
  text: string;
  className?: string;
}

export function LogTail({ text, className }: Props) {
  const ref = useRef<HTMLPreElement | null>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [text]);
  return (
    <pre
      ref={ref}
      className={cn(
        "max-h-[480px] overflow-auto rounded-md bg-slate-950 p-3 font-mono text-xs text-slate-100",
        className,
      )}
    >
      {text || "(no output)"}
    </pre>
  );
}
```

- [ ] **Step 3: Detector detail route**

Write `frontend/src/routes/_authed.detectors.$id.tsx`:

```tsx
import { useParams, Link } from "react-router";
import { useState } from "react";
import {
  useDetector,
  useDetectorVersion,
  useDetectorVersions,
  useDetectorBuilds,
  useAvailableTags,
  useTriggerBuild,
  useCancelBuild,
} from "@/api/queries/detectors";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataTable } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/common/StatusBadge";
import { JsonViewer } from "@/components/common/JsonViewer";
import { LogTail } from "@/components/common/LogTail";
import { formatRelative, formatDuration } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Detector" };

export default function DetectorDetailPage() {
  const { id = "" } = useParams();
  const { data: det } = useDetector(id);
  const { data: versions } = useDetectorVersions(id);
  const { data: builds } = useDetectorBuilds(id);
  const { data: tags } = useAvailableTags(id);
  const triggerBuild = useTriggerBuild(id);
  const cancelBuild = useCancelBuild(id);
  const [pickedTag, setPickedTag] = useState<string | null>(null);
  const [openSchemaTag, setOpenSchemaTag] = useState<string | null>(null);

  if (!det) return <p className="text-muted-foreground">Loading…</p>;

  const versionsArr =
    (versions as {
      tag: string;
      git_sha: string;
      status: string;
      built_at: string;
    }[]) ?? [];
  const buildsArr =
    (builds as {
      id: string;
      git_tag: string;
      status: string;
      started_at: string;
      finished_at: string | null;
      log_tail: string | null;
    }[]) ?? [];

  const versionsCols: ColumnDef<(typeof versionsArr)[number]>[] = [
    { accessorKey: "tag", header: "Tag" },
    {
      accessorKey: "git_sha",
      header: "Commit",
      cell: ({ row }) => (
        <span className="font-mono">{row.original.git_sha.slice(0, 10)}</span>
      ),
    },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
    {
      accessorKey: "built_at",
      header: "Built",
      cell: ({ row }) => formatRelative(row.original.built_at),
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setOpenSchemaTag(row.original.tag)}
        >
          View config schema
        </Button>
      ),
    },
  ];

  const buildsCols: ColumnDef<(typeof buildsArr)[number]>[] = [
    { accessorKey: "git_tag", header: "Tag" },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <StatusBadge status={row.original.status} />,
    },
    {
      accessorKey: "started_at",
      header: "Started",
      cell: ({ row }) => formatRelative(row.original.started_at),
    },
    {
      id: "duration",
      header: "Duration",
      cell: ({ row }) =>
        formatDuration(row.original.started_at, row.original.finished_at),
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <div className="flex gap-1">
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="ghost" size="sm">
                Logs
              </Button>
            </SheetTrigger>
            <SheetContent className="w-[600px] sm:max-w-[640px]">
              <SheetHeader>
                <SheetTitle>
                  Build {row.original.id.slice(0, 8)} — logs
                </SheetTitle>
              </SheetHeader>
              <div className="mt-4">
                <LogTail text={row.original.log_tail ?? "(no output)"} />
              </div>
            </SheetContent>
          </Sheet>
          {["pending", "building", "scanning"].includes(
            row.original.status,
          ) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => cancelBuild.mutate(row.original.id)}
            >
              Cancel
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{det.display_name}</h1>
        <Link to="/detectors" className="text-sm text-muted-foreground">
          ← back
        </Link>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="versions">Versions</TabsTrigger>
          <TabsTrigger value="builds">Builds</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <Card>
            <CardHeader>
              <CardTitle>Metadata</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div>
                <span className="text-muted-foreground">Name:</span>{" "}
                <code>{det.name}</code>
              </div>
              <div>
                <span className="text-muted-foreground">Git URL:</span>{" "}
                <code>{det.git_url}</code>
              </div>
              <div>
                <span className="text-muted-foreground">Description:</span>{" "}
                {det.description ?? "—"}
              </div>
              <div>
                <span className="text-muted-foreground">Created:</span>{" "}
                {formatRelative(det.created_at)}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="versions">
          <DataTable
            data={versionsArr}
            columns={versionsCols}
            emptyMessage="No versions built yet."
          />
          <Sheet
            open={!!openSchemaTag}
            onOpenChange={(o) => !o && setOpenSchemaTag(null)}
          >
            <SheetContent className="w-[720px] sm:max-w-[760px]">
              <SheetHeader>
                <SheetTitle>Config schema: {openSchemaTag}</SheetTitle>
              </SheetHeader>
              <div className="mt-4">
                <VersionSchemaView detectorId={id} tag={openSchemaTag ?? ""} />
              </div>
            </SheetContent>
          </Sheet>
        </TabsContent>

        <TabsContent value="builds">
          <div className="mb-3 flex justify-end">
            <Dialog>
              <DialogTrigger asChild>
                <Button>+ Trigger build</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Trigger build</DialogTitle>
                </DialogHeader>
                <div className="space-y-2">
                  <p className="text-sm text-muted-foreground">
                    Pick a git tag from the repository:
                  </p>
                  <Select value={pickedTag ?? ""} onValueChange={setPickedTag}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select tag" />
                    </SelectTrigger>
                    <SelectContent>
                      {(tags ?? []).map((t) => (
                        <SelectItem key={t.tag} value={t.tag}>
                          {t.tag} ({t.sha.slice(0, 7)})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <DialogFooter>
                  <Button
                    disabled={!pickedTag}
                    onClick={async () => {
                      await triggerBuild.mutateAsync({ git_tag: pickedTag! });
                      setPickedTag(null);
                    }}
                  >
                    Build
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
          <DataTable
            data={buildsArr}
            columns={buildsCols}
            emptyMessage="No builds yet."
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function VersionSchemaView({
  detectorId,
  tag,
}: {
  detectorId: string;
  tag: string;
}) {
  const { data } = useDetectorVersion(detectorId, tag);
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  return <JsonViewer value={data.config_schema} />;
}
```

- [ ] **Step 4: Wire route in `App.tsx`**

Add alongside other detectors routes:

```tsx
{ path: ":id", lazy: async () => ({
  Component: (await import("./routes/_authed.detectors.$id")).default,
  handle: (await import("./routes/_authed.detectors.$id")).handle,
})},
```

- [ ] **Step 5: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/routes/_authed.detectors.\$id.tsx frontend/src/components/common/ frontend/src/App.tsx
git commit -m "feat(frontend): detector detail page with versions + builds tabs"
```

---

## Task 22: Detector build E2E (detector-build.spec.ts)

**Files:**

- Create: `frontend/tests/e2e/detector-build.spec.ts`

- [ ] **Step 1: Write the spec**

Write `frontend/tests/e2e/detector-build.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("register upxelfdet + trigger build + wait for success", async ({
  page,
}) => {
  test.setTimeout(10 * 60_000); // builds can take a few minutes
  await login(page);

  // Ensure PAT is set (precondition) — if not, navigate to profile first
  await page.goto("/profile");
  const needsPat = await page
    .getByText(/GitHub PAT is set/i)
    .isVisible()
    .catch(() => false);
  if (!needsPat) {
    const token = process.env.E2E_GITHUB_PAT;
    test.skip(!token, "Set E2E_GITHUB_PAT to run this spec end-to-end.");
    await page.getByLabel(/GitHub PAT/i).fill(token!);
    await page.getByRole("button", { name: /^Save$/i }).click();
    await expect(page.getByText(/GitHub PAT is set/i)).toBeVisible();
  }

  // Go to detectors list; register upxelfdet if not already present
  await page.goto("/detectors");
  const existing = page.getByRole("cell", { name: /upx/i }).first();
  if (!(await existing.isVisible().catch(() => false))) {
    await page.getByRole("link", { name: /register/i }).click();
    await page.getByLabel(/^Name/i).fill("upxelfdet");
    await page.getByLabel(/Display name/i).fill("UPX ELF Detector");
    await page
      .getByLabel(/Git URL/i)
      .fill("https://github.com/bolin8017/upxelfdet");
    await page.getByRole("button", { name: /register detector/i }).click();
    await page.waitForURL(/\/detectors\/[0-9a-f-]+/);
  } else {
    await existing.click();
  }

  // Trigger build of v0.5.0 from Builds tab
  await page.getByRole("tab", { name: /builds/i }).click();
  await page.getByRole("button", { name: /trigger build/i }).click();
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: /v0\.5\.0/ }).click();
  await page.getByRole("button", { name: /^Build$/ }).click();

  // Wait for the newly-triggered build row to reach "Success"
  await expect(
    page.getByRole("cell", { name: /v0\.5\.0/ }).first(),
  ).toBeVisible();
  await expect(page.getByText(/Success/i).first()).toBeVisible({
    timeout: 8 * 60_000,
  });
});
```

- [ ] **Step 2: Run the spec (E2E_GITHUB_PAT required — give instructions to user)**

Tell user: "Set `export E2E_GITHUB_PAT=$(gh auth token)` before running." Then:

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm test:e2e detector-build
```

Expected: PASS within 8 minutes (build + scan).

- [ ] **Step 3: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/tests/e2e/detector-build.spec.ts
git commit -m "test(frontend): E2E detector registration + build"
```

---

## Task 23: Datasets query hooks + list route

**Files:**

- Create: `frontend/src/api/queries/datasets.ts`
- Create: `frontend/src/routes/_authed.datasets._index.tsx`

- [ ] **Step 1: Dataset queries**

Write `frontend/src/api/queries/datasets.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type Dataset = components["schemas"]["DatasetConfigRead"];

export const datasetsKeys = {
  all: ["datasets"] as const,
  list: (visibility: string) =>
    [...datasetsKeys.all, "list", visibility] as const,
  detail: (id: string) => [...datasetsKeys.all, "detail", id] as const,
};

export function useDatasets(visibility: "public" | "private" | "all" = "all") {
  return useQuery({
    queryKey: datasetsKeys.list(visibility),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/datasets", {
        params: { query: { visibility } },
      });
      if (error) throw error;
      return data;
    },
  });
}

export function useDataset(id: string) {
  return useQuery({
    queryKey: datasetsKeys.detail(id),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/datasets/{ds_id}", {
        params: { path: { ds_id: id } },
      });
      if (error) throw error;
      return data as Dataset;
    },
  });
}

export function useCreateDataset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["DatasetConfigCreate"]) => {
      const { data, error } = await client.POST("/api/v1/datasets", { body });
      if (error) throw error;
      return data as Dataset;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: datasetsKeys.all }),
  });
}

export function useDeleteDataset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await client.DELETE("/api/v1/datasets/{ds_id}", {
        params: { path: { ds_id: id } },
      });
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: datasetsKeys.all }),
  });
}
```

- [ ] **Step 2: Datasets list route**

Write `frontend/src/routes/_authed.datasets._index.tsx`:

```tsx
import { Link } from "react-router";
import { useState } from "react";
import { useDatasets, type Dataset } from "@/api/queries/datasets";
import { DataTable } from "@/components/tables/DataTable";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatRelative } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";

export const handle = { breadcrumb: "Datasets" };

const columns: ColumnDef<Dataset>[] = [
  { accessorKey: "name", header: "Name" },
  {
    accessorKey: "visibility",
    header: "Visibility",
    cell: ({ row }) => (
      <Badge
        variant={row.original.visibility === "public" ? "default" : "secondary"}
      >
        {row.original.visibility}
      </Badge>
    ),
  },
  { accessorKey: "sample_count", header: "Samples" },
  {
    accessorKey: "size_bytes",
    header: "Size",
    cell: ({ row }) => `${(row.original.size_bytes / 1024).toFixed(1)} KB`,
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => formatRelative(row.original.created_at),
  },
];

export default function DatasetsListPage() {
  const [visibility, setVisibility] = useState<"public" | "private" | "all">(
    "all",
  );
  const { data, isLoading } = useDatasets(visibility);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Datasets</h1>
        <div className="flex items-center gap-2">
          <Select
            value={visibility}
            onValueChange={(v) => setVisibility(v as typeof visibility)}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="public">Public</SelectItem>
              <SelectItem value="private">Mine (private)</SelectItem>
            </SelectContent>
          </Select>
          <Button asChild>
            <Link to="/datasets/new">
              <Plus className="mr-2 h-4 w-4" />
              Upload
            </Link>
          </Button>
        </div>
      </div>
      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : (
        <DataTable
          data={
            (data as { items?: Dataset[] })?.items ?? (data as Dataset[]) ?? []
          }
          columns={columns}
          emptyMessage="No datasets yet."
          onRowClick={(d) => {
            window.location.href = `/datasets/${d.id}`;
          }}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/api/queries/datasets.ts frontend/src/routes/_authed.datasets._index.tsx
git commit -m "feat(frontend): datasets list page"
```

---

## Task 24: Dataset upload form + route

**Files:**

- Create: `frontend/src/components/forms/DatasetUploadForm.tsx`
- Create: `frontend/src/routes/_authed.datasets.new.tsx`
- Create: `frontend/tests/unit/components/DatasetUploadForm.test.tsx`

- [ ] **Step 1: Unit test for size-limit validation**

Write `frontend/tests/unit/components/DatasetUploadForm.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import {
  checkCsvSize,
  MAX_CSV_BYTES,
} from "@/components/forms/DatasetUploadForm.logic";

describe("checkCsvSize", () => {
  it("accepts small CSV", () => {
    expect(checkCsvSize("a,b\n1,2\n")).toBeNull();
  });
  it("rejects > 10 MB", () => {
    const oversize = "a,b\n" + "x,y\n".repeat(Math.ceil(MAX_CSV_BYTES / 4));
    expect(checkCsvSize(oversize)).toMatch(/exceeds/i);
  });
});
```

- [ ] **Step 2: Extract pure logic**

Write `frontend/src/components/forms/DatasetUploadForm.logic.ts`:

```ts
export const MAX_CSV_BYTES = 10 * 1024 * 1024;

export function checkCsvSize(csv: string): string | null {
  const bytes = new Blob([csv]).size;
  if (bytes > MAX_CSV_BYTES) {
    return `CSV size ${(bytes / 1024 / 1024).toFixed(2)} MB exceeds limit of 10 MB`;
  }
  return null;
}
```

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm test -- DatasetUploadForm
```

Expected: PASS.

- [ ] **Step 3: Build form**

Write `frontend/src/components/forms/DatasetUploadForm.tsx`:

```tsx
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate } from "react-router";
import { useCreateDataset } from "@/api/queries/datasets";
import { parseCsvPreview, type CsvPreview } from "@/lib/csv";
import { checkCsvSize } from "./DatasetUploadForm.logic";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { applyFieldErrorsToForm } from "@/lib/errors";
import type { LoldayApiError } from "@/api/errors";

const schema = z.object({
  name: z.string().min(1).max(100),
  description: z.string().optional(),
  visibility: z.enum(["public", "private"]).default("public"),
  csv_content: z.string().min(1, "CSV content is required"),
});
type Values = z.infer<typeof schema>;

export function DatasetUploadForm() {
  const nav = useNavigate();
  const mut = useCreateDataset();
  const {
    register,
    handleSubmit,
    setValue,
    setError,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { visibility: "public" },
  });
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);

  const content = watch("csv_content");

  async function onFilePick(ev: React.ChangeEvent<HTMLInputElement>) {
    const file = ev.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    setValue("csv_content", text, { shouldValidate: true });
    runPreview(text);
  }

  function runPreview(text: string) {
    setParseError(null);
    const sizeErr = checkCsvSize(text);
    if (sizeErr) {
      setParseError(sizeErr);
      setPreview(null);
      return;
    }
    try {
      setPreview(parseCsvPreview(text, 10));
    } catch (e) {
      setParseError((e as Error).message);
      setPreview(null);
    }
  }

  const onSubmit = handleSubmit(async (v) => {
    const sizeErr = checkCsvSize(v.csv_content);
    if (sizeErr) {
      setError("csv_content", { message: sizeErr });
      return;
    }
    try {
      const ds = await mut.mutateAsync(v);
      nav(`/datasets/${ds.id}`);
    } catch (e) {
      applyFieldErrorsToForm(e as LoldayApiError, setError);
    }
  });

  return (
    <form className="space-y-4 max-w-2xl" onSubmit={onSubmit}>
      <div>
        <Label htmlFor="name">Name</Label>
        <Input id="name" placeholder="upx-train-v3" {...register("name")} />
        {errors.name && (
          <p className="text-xs text-destructive">{errors.name.message}</p>
        )}
      </div>
      <div>
        <Label htmlFor="description">Description</Label>
        <Textarea id="description" rows={2} {...register("description")} />
      </div>
      <div>
        <Label htmlFor="visibility">Visibility</Label>
        <select
          id="visibility"
          className="block w-full rounded-md border p-2"
          {...register("visibility")}
        >
          <option value="public">Public (all lab members)</option>
          <option value="private">Private (me + admin)</option>
        </select>
      </div>

      <div className="space-y-2">
        <Label>CSV content</Label>
        <Tabs defaultValue="file">
          <TabsList>
            <TabsTrigger value="file">File picker</TabsTrigger>
            <TabsTrigger value="paste">Paste</TabsTrigger>
          </TabsList>
          <TabsContent value="file">
            <Input type="file" accept=".csv,text/csv" onChange={onFilePick} />
          </TabsContent>
          <TabsContent value="paste">
            <Textarea
              rows={8}
              placeholder="file_name,label,family&#10;abc…,Malware,mirai"
              value={content ?? ""}
              onChange={(e) => {
                setValue("csv_content", e.target.value);
                runPreview(e.target.value);
              }}
            />
          </TabsContent>
        </Tabs>
        {errors.csv_content && (
          <p className="text-xs text-destructive">
            {errors.csv_content.message}
          </p>
        )}
        {parseError && (
          <Alert variant="destructive">
            <AlertDescription>{parseError}</AlertDescription>
          </Alert>
        )}
        {preview && (
          <div className="rounded border p-2 text-xs">
            <p className="text-muted-foreground mb-1">
              Preview ({preview.rows.length} of {preview.totalRows} rows)
            </p>
            <table className="w-full">
              <thead>
                <tr>
                  {preview.columns.map((c) => (
                    <th key={c} className="text-left">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((r, i) => (
                  <tr key={i}>
                    {preview.columns.map((c) => (
                      <td key={c} className="truncate">
                        {r[c]}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Button type="submit" disabled={isSubmitting || !!parseError}>
        Upload dataset
      </Button>
    </form>
  );
}
```

- [ ] **Step 4: Route**

Write `frontend/src/routes/_authed.datasets.new.tsx`:

```tsx
import { DatasetUploadForm } from "@/components/forms/DatasetUploadForm";
export const handle = { breadcrumb: "New dataset" };
export default function NewDatasetPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Upload dataset</h1>
      <DatasetUploadForm />
    </div>
  );
}
```

- [ ] **Step 5: Wire + commit**

Add to `App.tsx` under `datasets` children: `{ path: "new", lazy: … }`.

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/forms/DatasetUploadForm.tsx frontend/src/components/forms/DatasetUploadForm.logic.ts frontend/src/routes/_authed.datasets.new.tsx frontend/tests/unit/components/DatasetUploadForm.test.tsx frontend/src/App.tsx
git commit -m "feat(frontend): dataset upload form with client-side CSV preview"
```

---

## Task 25: Dataset detail + charts + delete

**Files:**

- Create: `frontend/src/components/charts/LabelDistribution.tsx`
- Create: `frontend/src/components/charts/FamilyDistribution.tsx`
- Create: `frontend/src/routes/_authed.datasets.$id.tsx`
- Create: `frontend/tests/e2e/dataset-upload.spec.ts`
- Create: `frontend/tests/e2e/fixtures/small-dataset.csv`

- [ ] **Step 1: LabelDistribution (Recharts Pie)**

Write `frontend/src/components/charts/LabelDistribution.tsx`:

```tsx
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Legend,
  Tooltip,
} from "recharts";

const COLORS = ["#dc2626", "#16a34a", "#f59e0b", "#0ea5e9", "#8b5cf6"];

export function LabelDistribution({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).map(([name, value]) => ({
    name,
    value,
  }));
  if (entries.length === 0)
    return <p className="text-muted-foreground">No label data.</p>;
  return (
    <div style={{ width: "100%", height: 260 }}>
      <ResponsiveContainer>
        <PieChart>
          <Pie
            data={entries}
            dataKey="value"
            nameKey="name"
            outerRadius={90}
            label
          >
            {entries.map((_, i) => (
              <Cell key={i} fill={COLORS[i % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 2: FamilyDistribution (Recharts Bar, top 15)**

Write `frontend/src/components/charts/FamilyDistribution.tsx`:

```tsx
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";

export function FamilyDistribution({ data }: { data: Record<string, number> }) {
  const top = Object.entries(data)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 15)
    .map(([name, value]) => ({ name, value }));
  if (top.length === 0)
    return <p className="text-muted-foreground">No family data.</p>;
  return (
    <div style={{ width: "100%", height: 300 }}>
      <ResponsiveContainer>
        <BarChart data={top} layout="vertical" margin={{ left: 60 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" />
          <YAxis type="category" dataKey="name" width={120} />
          <Tooltip />
          <Bar dataKey="value" fill="#0ea5e9" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
```

- [ ] **Step 3: Detail route**

Write `frontend/src/routes/_authed.datasets.$id.tsx`:

```tsx
import { useParams, useNavigate } from "react-router";
import { useDataset, useDeleteDataset } from "@/api/queries/datasets";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { LabelDistribution } from "@/components/charts/LabelDistribution";
import { FamilyDistribution } from "@/components/charts/FamilyDistribution";
import { formatRelative } from "@/lib/date";

export const handle = { breadcrumb: "Dataset" };

export default function DatasetDetailPage() {
  const { id = "" } = useParams();
  const { data } = useDataset(id);
  const nav = useNavigate();
  const del = useDeleteDataset();
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{data.name}</h1>
          <p className="text-sm text-muted-foreground">
            {data.description ?? "—"}
          </p>
        </div>
        <Button
          variant="destructive"
          onClick={async () => {
            if (!confirm("Delete this dataset?")) return;
            await del.mutateAsync(id);
            nav("/datasets");
          }}
        >
          Delete
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Metadata</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span className="text-muted-foreground">Visibility:</span>{" "}
            <Badge>{data.visibility}</Badge>
          </div>
          <div>
            <span className="text-muted-foreground">Samples:</span>{" "}
            {data.sample_count.toLocaleString()}
          </div>
          <div>
            <span className="text-muted-foreground">Size:</span>{" "}
            {(data.size_bytes / 1024).toFixed(1)} KB
          </div>
          <div>
            <span className="text-muted-foreground">Created:</span>{" "}
            {formatRelative(data.created_at)}
          </div>
          <div className="col-span-2">
            <span className="text-muted-foreground">Checksum:</span>{" "}
            <code className="text-xs">{data.csv_checksum}</code>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Label distribution</CardTitle>
          </CardHeader>
          <CardContent>
            <LabelDistribution
              data={data.label_distribution as Record<string, number>}
            />
          </CardContent>
        </Card>
        {data.family_distribution && (
          <Card>
            <CardHeader>
              <CardTitle>Top 15 families</CardTitle>
            </CardHeader>
            <CardContent>
              <FamilyDistribution
                data={data.family_distribution as Record<string, number>}
              />
            </CardContent>
          </Card>
        )}
      </div>

      <div>
        <a
          href={`${import.meta.env.VITE_API_BASE}/datasets/${id}/csv`}
          className="text-sm underline"
        >
          Download CSV
        </a>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create test fixture**

Write `frontend/tests/e2e/fixtures/small-dataset.csv`:

```
file_name,label,family
0000002158d35c2bb5e7d96a39ff464ea4c83de8c5fd72094736f79125aaca11,Malware,mirai
00000391058cf784a3e1a3f4babfb2e02b74857178cfdc39a7f833631c0a5a35,Malware,xorddos
ff0022f25aa3c91b8ad6a6e1920a71b8d5ab02cb4b37c63f12d091e29e9a8e1c,Benign,
```

(In real E2E, a proper 100-row fixture is seeded — this is the minimum-viable version.)

- [ ] **Step 5: E2E**

Write `frontend/tests/e2e/dataset-upload.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import path from "node:path";
import { login } from "./helpers";

test("upload dataset and see stats", async ({ page }) => {
  await login(page);
  await page.goto("/datasets/new");
  await page.getByLabel(/^Name$/).fill(`e2e-${Date.now()}`);
  await page.getByRole("tab", { name: /file picker/i }).click();
  await page.setInputFiles(
    'input[type="file"]',
    path.resolve(__dirname, "fixtures/small-dataset.csv"),
  );
  await expect(page.getByText(/Preview \(3 of 3 rows\)/)).toBeVisible();
  await page.getByRole("button", { name: /upload dataset/i }).click();
  await page.waitForURL(/\/datasets\/[0-9a-f-]+/);
  await expect(page.getByText(/Label distribution/)).toBeVisible();
});
```

- [ ] **Step 6: Wire + run + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/charts/ frontend/src/routes/_authed.datasets.\$id.tsx frontend/tests/e2e/dataset-upload.spec.ts frontend/tests/e2e/fixtures/ frontend/src/App.tsx
git commit -m "feat(frontend): dataset detail with charts + dataset-upload E2E"
```

---

## Task 26: Jobs query hooks + list route

**Files:**

- Create: `frontend/src/api/queries/jobs.ts`
- Create: `frontend/src/routes/_authed.jobs._index.tsx`

- [ ] **Step 1: Jobs queries**

Write `frontend/src/api/queries/jobs.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";
import { NON_TERMINAL_JOB_STATUSES } from "@/lib/status";

export type Job = components["schemas"]["JobRead"];
export type JobType = "train" | "evaluate" | "predict";

export const jobsKeys = {
  all: ["jobs"] as const,
  list: (params: Record<string, unknown>) =>
    [...jobsKeys.all, "list", params] as const,
  detail: (id: string) => [...jobsKeys.all, "detail", id] as const,
  logs: (id: string) => [...jobsKeys.all, "logs", id] as const,
};

const isActive = (s: string | undefined) =>
  s ? (NON_TERMINAL_JOB_STATUSES as readonly string[]).includes(s) : false;

export function useJobs(
  params: { type?: JobType; status?: string; owner?: "me" | "all" } = {},
) {
  return useQuery({
    queryKey: jobsKeys.list(params),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/jobs", {
        params: { query: params },
      });
      if (error) throw error;
      return data;
    },
    refetchInterval: 5000, // list: mild refresh for visible active jobs
  });
}

export function useJob(id: string) {
  return useQuery({
    queryKey: jobsKeys.detail(id),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/jobs/{job_id}", {
        params: { path: { job_id: id } },
      });
      if (error) throw error;
      return data as Job;
    },
    refetchInterval: (q) =>
      isActive((q.state.data as { data?: Job } | undefined)?.data?.status)
        ? 2000
        : false,
  });
}

export function useJobLogs(id: string, jobStatus: string | undefined) {
  return useQuery({
    queryKey: jobsKeys.logs(id),
    queryFn: async () => {
      const resp = await fetch(
        `${import.meta.env.VITE_API_BASE}/jobs/${id}/logs`,
        { credentials: "include" },
      );
      return resp.text();
    },
    refetchInterval: isActive(jobStatus) ? 2000 : false,
  });
}

export function useSubmitJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["JobCreate"]) => {
      const { data, error } = await client.POST("/api/v1/jobs", { body });
      if (error) throw error;
      return data as Job;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: jobsKeys.all }),
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error } = await client.POST(
        "/api/v1/jobs/{job_id}/cancel",
        {
          params: { path: { job_id: id } },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: jobsKeys.all }),
  });
}
```

- [ ] **Step 2: Jobs list route**

Write `frontend/src/routes/_authed.jobs._index.tsx`:

```tsx
import { Link } from "react-router";
import { useState } from "react";
import { useJobs, type Job, type JobType } from "@/api/queries/jobs";
import { DataTable } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatRelative, formatDuration } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";

export const handle = { breadcrumb: "Jobs" };

const columns: ColumnDef<Job>[] = [
  {
    accessorKey: "type",
    header: "Type",
    cell: ({ row }) => <Badge variant="outline">{row.original.type}</Badge>,
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
  },
  {
    accessorKey: "submitted_at",
    header: "Submitted",
    cell: ({ row }) => formatRelative(row.original.submitted_at),
  },
  {
    id: "duration",
    header: "Duration",
    cell: ({ row }) =>
      formatDuration(row.original.started_at, row.original.finished_at),
  },
];

export default function JobsListPage() {
  const [type, setType] = useState<JobType | "all">("all");
  const params = type === "all" ? {} : { type };
  const { data, isLoading } = useJobs(params);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Jobs</h1>
        <div className="flex items-center gap-2">
          <Select value={type} onValueChange={(v) => setType(v as typeof type)}>
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              <SelectItem value="train">Train</SelectItem>
              <SelectItem value="evaluate">Evaluate</SelectItem>
              <SelectItem value="predict">Predict</SelectItem>
            </SelectContent>
          </Select>
          <Button asChild>
            <Link to="/jobs/new">
              <Plus className="mr-2 h-4 w-4" />
              Submit job
            </Link>
          </Button>
        </div>
      </div>
      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : (
        <DataTable
          data={(data as { items?: Job[] })?.items ?? (data as Job[]) ?? []}
          columns={columns}
          emptyMessage="No jobs yet."
          onRowClick={(j) => {
            window.location.href = `/jobs/${j.id}`;
          }}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/api/queries/jobs.ts frontend/src/routes/_authed.jobs._index.tsx frontend/src/App.tsx
git commit -m "feat(frontend): jobs list page"
```

---

## Task 27: RJSF config form component

**Files:**

- Create: `frontend/src/components/forms/RjsfConfigForm.tsx`

- [ ] **Step 1: Install rjsf-tailwind theme**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm add @rjsf-tailwind/rjsf-tailwind@^5 || pnpm add @rjsf/core@^5
```

> If `rjsf-tailwind` is unavailable on npm at build time, fallback to `@rjsf/core` with default theme and wrap in Tailwind styles manually — document in Open Question #1.

- [ ] **Step 2: RjsfConfigForm**

Write `frontend/src/components/forms/RjsfConfigForm.tsx`:

```tsx
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";

interface Props {
  schema: object;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}

export function RjsfConfigForm({ schema, value, onChange }: Props) {
  return (
    <div className="rjsf-wrap rounded-md border bg-card p-4 text-sm">
      <Form
        schema={schema}
        validator={validator}
        formData={value}
        liveValidate
        showErrorList={false}
        onChange={(e) => onChange(e.formData as Record<string, unknown>)}
        uiSchema={{ "ui:submitButtonOptions": { norender: true } }}
      >
        {/* No submit — parent form owns submission */}
        <span />
      </Form>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/forms/RjsfConfigForm.tsx frontend/package.json frontend/pnpm-lock.yaml
git commit -m "feat(frontend): RJSF config form wrapper for dynamic detector params"
```

---

## Task 28: Job submit form (single-page progressive disclosure)

> **Prereq:** `JobSubmitForm` imports `useRegisteredModels` + `useModelVersions` from `@/api/queries/models`. That file is introduced in Task 35. Step 0 below creates a minimal stub so this task compiles now; Task 35 expands the file rather than recreating it.

**Files:**

- Create (stub expanded in Task 35): `frontend/src/api/queries/models.ts`
- Create: `frontend/src/components/forms/JobSubmitForm.tsx`
- Create: `frontend/src/routes/_authed.jobs.new.tsx`
- Create: `frontend/tests/unit/components/JobSubmitForm.test.tsx`

- [ ] **Step 0: Create minimal models queries stub**

Write `frontend/src/api/queries/models.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { client } from "@/api/client";

export const modelsKeys = {
  all: ["models"] as const,
  list: () => [...modelsKeys.all, "list"] as const,
  versions: (name: string) => [...modelsKeys.all, "versions", name] as const,
};

export function useRegisteredModels() {
  return useQuery({
    queryKey: modelsKeys.list(),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models");
      if (error) throw error;
      return data as { name: string }[];
    },
  });
}

export function useModelVersions(name: string) {
  return useQuery({
    queryKey: modelsKeys.versions(name),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/models/{name}/versions",
        {
          params: { path: { name } },
        },
      );
      if (error) throw error;
      return data;
    },
    enabled: Boolean(name),
  });
}
```

Task 35 replaces this file with the full version (adds `useModelDetail`, `useTransitionModel`, typed schemas).

- [ ] **Step 1: Unit test for field-requirement logic**

Write `frontend/tests/unit/components/JobSubmitForm.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { requiredFieldsForType } from "@/components/forms/JobSubmitForm.logic";

describe("requiredFieldsForType", () => {
  it("train needs train+test datasets", () => {
    expect(requiredFieldsForType("train")).toEqual([
      "train_dataset_id",
      "test_dataset_id",
    ]);
  });
  it("evaluate needs test+source_model", () => {
    expect(requiredFieldsForType("evaluate")).toEqual([
      "test_dataset_id",
      "source_model_version_id",
    ]);
  });
  it("predict needs predict+source_model", () => {
    expect(requiredFieldsForType("predict")).toEqual([
      "predict_dataset_id",
      "source_model_version_id",
    ]);
  });
});
```

- [ ] **Step 2: Pure logic file**

Write `frontend/src/components/forms/JobSubmitForm.logic.ts`:

```ts
import type { JobType } from "@/api/queries/jobs";

export function requiredFieldsForType(type: JobType): string[] {
  switch (type) {
    case "train":
      return ["train_dataset_id", "test_dataset_id"];
    case "evaluate":
      return ["test_dataset_id", "source_model_version_id"];
    case "predict":
      return ["predict_dataset_id", "source_model_version_id"];
  }
}
```

```bash
pnpm test -- JobSubmitForm
```

Expected: PASS.

- [ ] **Step 3: Build JobSubmitForm**

Write `frontend/src/components/forms/JobSubmitForm.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import {
  useDetectors,
  useDetectorVersions,
  useDetectorVersion,
} from "@/api/queries/detectors";
import { useDatasets } from "@/api/queries/datasets";
import { useRegisteredModels, useModelVersions } from "@/api/queries/models";
import { useSubmitJob, useJob, type JobType } from "@/api/queries/jobs";
import { RjsfConfigForm } from "./RjsfConfigForm";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { requiredFieldsForType } from "./JobSubmitForm.logic";

const TYPES: JobType[] = ["train", "evaluate", "predict"];

export function JobSubmitForm() {
  const [params] = useSearchParams();
  const fromJobId = params.get("from");
  const { data: fromJob } = useJob(fromJobId ?? "");

  const [type, setType] = useState<JobType>("train");
  const [detectorId, setDetectorId] = useState("");
  const [versionTag, setVersionTag] = useState("");
  const [trainDatasetId, setTrainDatasetId] = useState("");
  const [testDatasetId, setTestDatasetId] = useState("");
  const [predictDatasetId, setPredictDatasetId] = useState("");
  const [sourceModelName, setSourceModelName] = useState("");
  const [sourceModelVersionId, setSourceModelVersionId] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);

  const { data: detectors } = useDetectors();
  const { data: versions } = useDetectorVersions(detectorId);
  const { data: versionDetail } = useDetectorVersion(detectorId, versionTag);
  const { data: datasets } = useDatasets("all");
  const { data: models } = useRegisteredModels();
  const { data: modelVersions } = useModelVersions(sourceModelName);

  // Prefill from previous job via ?from=
  useEffect(() => {
    if (!fromJob) return;
    setType(fromJob.type as JobType);
    if (fromJob.train_dataset_id) setTrainDatasetId(fromJob.train_dataset_id);
    if (fromJob.test_dataset_id) setTestDatasetId(fromJob.test_dataset_id);
    if (fromJob.predict_dataset_id)
      setPredictDatasetId(fromJob.predict_dataset_id);
    if (fromJob.resolved_config)
      setConfig(fromJob.resolved_config as Record<string, unknown>);
  }, [fromJob]);

  const datasetsArr =
    (datasets as { items?: { id: string; name: string }[] })?.items ??
    (datasets as { id: string; name: string }[]) ??
    [];
  const versionsArr =
    (versions as { tag: string; status: string }[] | undefined) ?? [];
  const modelsArr = (models as { name: string }[] | undefined) ?? [];
  const modelVersionsArr =
    (
      modelVersions as {
        items?: { id: string; mlflow_version: number; current_stage: string }[];
      }
    )?.items ?? [];

  const mut = useSubmitJob();
  const nav = useNavigate();

  const canSubmit = (() => {
    if (!detectorId || !versionTag) return false;
    const need = requiredFieldsForType(type);
    if (need.includes("train_dataset_id") && !trainDatasetId) return false;
    if (need.includes("test_dataset_id") && !testDatasetId) return false;
    if (need.includes("predict_dataset_id") && !predictDatasetId) return false;
    if (need.includes("source_model_version_id") && !sourceModelVersionId)
      return false;
    return true;
  })();

  async function submit() {
    setSubmitError(null);
    const versionId = (
      versions as { id: string; tag: string }[] | undefined
    )?.find((v) => v.tag === versionTag)?.id;
    if (!versionId) return;
    try {
      const job = await mut.mutateAsync({
        type,
        detector_version_id: versionId,
        train_dataset_id: type === "train" ? trainDatasetId : null,
        test_dataset_id: ["train", "evaluate"].includes(type)
          ? testDatasetId
          : null,
        predict_dataset_id: type === "predict" ? predictDatasetId : null,
        source_model_version_id: ["evaluate", "predict"].includes(type)
          ? sourceModelVersionId
          : null,
        params: config,
      } as unknown as import("@/api/schema.gen").components["schemas"]["JobCreate"]);
      nav(`/jobs/${job.id}`);
    } catch (e) {
      setSubmitError((e as { detail?: string }).detail ?? "Submit failed");
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <Card>
        <CardHeader>
          <CardTitle>Job type</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            {TYPES.map((t) => (
              <Button
                key={t}
                variant={t === type ? "default" : "outline"}
                onClick={() => setType(t)}
              >
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Detector</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label>Detector</Label>
            <Select
              value={detectorId}
              onValueChange={(v) => {
                setDetectorId(v);
                setVersionTag("");
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick detector" />
              </SelectTrigger>
              <SelectContent>
                {(
                  (detectors as { id: string; display_name: string }[]) ?? []
                ).map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Version</Label>
            <Select
              value={versionTag}
              onValueChange={setVersionTag}
              disabled={!detectorId}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick version" />
              </SelectTrigger>
              <SelectContent>
                {versionsArr
                  .filter((v) => v.status === "active")
                  .map((v) => (
                    <SelectItem key={v.tag} value={v.tag}>
                      {v.tag}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Data</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {type === "train" && (
            <>
              <DatasetField
                label="Train dataset"
                value={trainDatasetId}
                onChange={setTrainDatasetId}
                options={datasetsArr}
              />
              <DatasetField
                label="Test dataset"
                value={testDatasetId}
                onChange={setTestDatasetId}
                options={datasetsArr}
              />
            </>
          )}
          {type === "evaluate" && (
            <DatasetField
              label="Test dataset"
              value={testDatasetId}
              onChange={setTestDatasetId}
              options={datasetsArr}
            />
          )}
          {type === "predict" && (
            <DatasetField
              label="Predict dataset"
              value={predictDatasetId}
              onChange={setPredictDatasetId}
              options={datasetsArr}
            />
          )}
          {["evaluate", "predict"].includes(type) && (
            <>
              <div>
                <Label>Source model</Label>
                <Select
                  value={sourceModelName}
                  onValueChange={(v) => {
                    setSourceModelName(v);
                    setSourceModelVersionId("");
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Pick model" />
                  </SelectTrigger>
                  <SelectContent>
                    {modelsArr.map((m) => (
                      <SelectItem key={m.name} value={m.name}>
                        {m.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Model version</Label>
                <Select
                  value={sourceModelVersionId}
                  onValueChange={setSourceModelVersionId}
                  disabled={!sourceModelName}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Pick version" />
                  </SelectTrigger>
                  <SelectContent>
                    {modelVersionsArr.map((mv) => (
                      <SelectItem key={mv.id} value={mv.id}>
                        v{mv.mlflow_version} ({mv.current_stage})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Hyperparameters</CardTitle>
        </CardHeader>
        <CardContent>
          {versionDetail?.config_schema ? (
            <RjsfConfigForm
              schema={versionDetail.config_schema as object}
              value={config}
              onChange={setConfig}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              Pick a detector + version to load its config schema.
            </p>
          )}
        </CardContent>
      </Card>

      {submitError && <p className="text-sm text-destructive">{submitError}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => nav(-1)}>
          Cancel
        </Button>
        <Button disabled={!canSubmit || mut.isPending} onClick={submit}>
          Submit job
        </Button>
      </div>
    </div>
  );
}

function DatasetField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { id: string; name: string }[];
}) {
  return (
    <div>
      <Label>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger>
          <SelectValue placeholder="Pick dataset" />
        </SelectTrigger>
        <SelectContent>
          {options.map((d) => (
            <SelectItem key={d.id} value={d.id}>
              {d.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
```

- [ ] **Step 4: Route**

Write `frontend/src/routes/_authed.jobs.new.tsx`:

```tsx
import { JobSubmitForm } from "@/components/forms/JobSubmitForm";
export const handle = { breadcrumb: "New job" };
export default function NewJobPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Submit job</h1>
      <JobSubmitForm />
    </div>
  );
}
```

- [ ] **Step 5: Wire + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/forms/JobSubmitForm.tsx frontend/src/components/forms/JobSubmitForm.logic.ts frontend/src/routes/_authed.jobs.new.tsx frontend/tests/unit/components/JobSubmitForm.test.tsx frontend/src/App.tsx
git commit -m "feat(frontend): job submit form (single-page progressive disclosure + RJSF)"
```

---

## Task 29: Artifact tree + MetricCards + ConfusionMatrix

**Files:**

- Create: `frontend/src/components/common/ArtifactTree.tsx`
- Create: `frontend/src/components/charts/MetricCards.tsx`
- Create: `frontend/src/components/charts/ConfusionMatrix.tsx`
- Create: `frontend/tests/unit/components/ConfusionMatrix.test.tsx`

- [ ] **Step 1: MetricCards**

Write `frontend/src/components/charts/MetricCards.tsx`:

```tsx
import { Card, CardContent } from "@/components/ui/card";

export function MetricCards({ metrics }: { metrics: Record<string, number> }) {
  const keys = ["accuracy", "precision", "recall", "f1", "f1_score"];
  const entries = keys
    .map((k) => [k, metrics[k]] as const)
    .filter(([, v]) => typeof v === "number");
  if (entries.length === 0)
    return (
      <p className="text-muted-foreground text-sm">No metrics recorded yet.</p>
    );
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {entries.map(([k, v]) => (
        <Card key={k}>
          <CardContent className="p-4">
            <div className="text-xs uppercase text-muted-foreground">
              {k.replace("_score", "")}
            </div>
            <div className="text-2xl font-semibold">
              {(v as number).toFixed(4)}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: ConfusionMatrix unit test**

Write `frontend/tests/unit/components/ConfusionMatrix.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { cellColor } from "@/components/charts/ConfusionMatrix";

describe("ConfusionMatrix cellColor", () => {
  it("returns success tone for diagonal", () => {
    expect(cellColor(0, 0, true)).toBe("success");
    expect(cellColor(1, 1, true)).toBe("success");
  });
  it("returns warn for off-diagonal", () => {
    expect(cellColor(0, 1, false)).toBe("warn");
  });
});
```

- [ ] **Step 3: ConfusionMatrix**

Write `frontend/src/components/charts/ConfusionMatrix.tsx`:

```tsx
import { cn } from "@/lib/cn";

export type CellTone = "success" | "warn";

export function cellColor(
  row: number,
  col: number,
  onDiagonal: boolean,
): CellTone {
  return onDiagonal ? "success" : "warn";
}

const TONE_CLASSES: Record<CellTone, string> = {
  success: "bg-emerald-500 text-white",
  warn: "bg-rose-100 text-rose-900",
};

interface Props {
  labels: string[];
  matrix: number[][]; // row = true label, col = predicted
}

export function ConfusionMatrix({ labels, matrix }: Props) {
  return (
    <div className="inline-block">
      <div
        className="grid gap-1"
        style={{
          gridTemplateColumns: `auto repeat(${labels.length}, minmax(4rem, 1fr))`,
        }}
      >
        <div />
        {labels.map((l) => (
          <div
            key={`col-${l}`}
            className="px-2 py-1 text-center text-xs font-medium text-muted-foreground"
          >
            Pred {l}
          </div>
        ))}
        {matrix.map((row, i) => (
          <>
            <div
              key={`row-${labels[i]}`}
              className="px-2 py-1 text-right text-xs font-medium text-muted-foreground"
            >
              True {labels[i]}
            </div>
            {row.map((v, j) => {
              const tone = cellColor(i, j, i === j);
              return (
                <div
                  key={`cell-${i}-${j}`}
                  className={cn(
                    "rounded px-3 py-2 text-center font-mono text-sm",
                    TONE_CLASSES[tone],
                  )}
                >
                  {v}
                </div>
              );
            })}
          </>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: ArtifactTree (fetch subdirectories on expand)**

Write `frontend/src/components/common/ArtifactTree.tsx`:

```tsx
import { useState } from "react";
import { client } from "@/api/client";
import { useQuery } from "@tanstack/react-query";
import { Folder, FileText, Download } from "lucide-react";
import { cn } from "@/lib/cn";

interface Entry {
  path: string;
  is_dir: boolean;
  file_size: number;
}

function useArtifacts(runId: string, path: string | null) {
  return useQuery({
    queryKey: ["runs", runId, "artifacts", path ?? ""],
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/runs/{run_id}/artifacts",
        {
          params: { path: { run_id: runId }, query: path ? { path } : {} },
        },
      );
      if (error) throw error;
      return (data as { files?: Entry[] }).files ?? [];
    },
  });
}

function TreeLevel({
  runId,
  path,
  depth,
}: {
  runId: string;
  path: string | null;
  depth: number;
}) {
  const { data, isLoading } = useArtifacts(runId, path);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  if (isLoading)
    return <p className="ml-4 text-xs text-muted-foreground">Loading…</p>;
  if (!data || data.length === 0)
    return <p className="ml-4 text-xs text-muted-foreground">(empty)</p>;
  if (depth >= 10)
    return <p className="ml-4 text-xs text-destructive">(tree too deep)</p>;

  return (
    <ul className="space-y-1">
      {data.map((e) => {
        const name = e.path.split("/").pop() ?? e.path;
        const isExpanded = expanded.has(e.path);
        return (
          <li key={e.path} className={cn("rounded px-2", depth > 0 && "ml-4")}>
            <div className="flex items-center gap-2 py-1">
              {e.is_dir ? (
                <button
                  className="flex items-center gap-1 text-sm hover:underline"
                  onClick={() =>
                    setExpanded((s) => {
                      const n = new Set(s);
                      n.has(e.path) ? n.delete(e.path) : n.add(e.path);
                      return n;
                    })
                  }
                >
                  <Folder className="h-4 w-4" /> {name}
                </button>
              ) : (
                <>
                  <FileText className="h-4 w-4" />
                  <span className="flex-1 text-sm">{name}</span>
                  <a
                    className="inline-flex items-center text-xs text-primary hover:underline"
                    href={`${import.meta.env.VITE_API_BASE}/runs/${runId}/artifacts/download?path=${encodeURIComponent(e.path)}`}
                  >
                    <Download className="mr-1 h-3 w-3" />
                    download
                  </a>
                </>
              )}
            </div>
            {e.is_dir && isExpanded && (
              <TreeLevel runId={runId} path={e.path} depth={depth + 1} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function ArtifactTree({ runId }: { runId: string }) {
  return <TreeLevel runId={runId} path={null} depth={0} />;
}
```

- [ ] **Step 5: Run tests + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend && pnpm test
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/common/ArtifactTree.tsx frontend/src/components/charts/ frontend/tests/unit/components/ConfusionMatrix.test.tsx
git commit -m "feat(frontend): metric cards + confusion matrix + artifact tree"
```

---

## Task 30: Job detail route (Summary / Logs / Artifacts)

**Files:**

- Create: `frontend/src/routes/_authed.jobs.$id.tsx`

- [ ] **Step 1: Detail route**

Write `frontend/src/routes/_authed.jobs.$id.tsx`:

```tsx
import { useParams, Link, useNavigate } from "react-router";
import { useJob, useJobLogs, useCancelJob } from "@/api/queries/jobs";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/common/StatusBadge";
import { MetricCards } from "@/components/charts/MetricCards";
import { ConfusionMatrix } from "@/components/charts/ConfusionMatrix";
import { ArtifactTree } from "@/components/common/ArtifactTree";
import { LogTail } from "@/components/common/LogTail";
import { JsonViewer } from "@/components/common/JsonViewer";
import { formatDuration, formatRelative } from "@/lib/date";
import { isTerminal } from "@/lib/status";

export const handle = { breadcrumb: "Job" };

export default function JobDetailPage() {
  const { id = "" } = useParams();
  const { data: job } = useJob(id);
  const { data: logText } = useJobLogs(id, job?.status);
  const cancel = useCancelJob();
  const nav = useNavigate();
  if (!job) return <p className="text-muted-foreground">Loading…</p>;

  const sm = (job.summary_metrics ?? {}) as Record<string, unknown>;
  const metrics =
    typeof sm.metrics === "object" && sm.metrics
      ? (sm.metrics as Record<string, number>)
      : {};
  const cm = sm.confusion_matrix as
    | { labels?: string[]; matrix?: number[][] }
    | undefined;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">
            {job.type} — {id.slice(0, 8)}
          </h1>
          <StatusBadge status={job.status} />
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => nav(`/jobs/new?from=${id}`)}>
            Clone
          </Button>
          {!isTerminal(job.status) && (
            <Button variant="destructive" onClick={() => cancel.mutate(id)}>
              Cancel
            </Button>
          )}
        </div>
      </div>

      <Tabs defaultValue="summary">
        <TabsList>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
          <TabsTrigger value="artifacts" disabled={!job.mlflow_run_id}>
            Artifacts
          </TabsTrigger>
          {job.mlflow_run_id && (
            <TabsTrigger value="mlflow" asChild>
              <Link
                to={`/runs/${job.mlflow_experiment_id}/${job.mlflow_run_id}`}
              >
                Open run ↗
              </Link>
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="summary" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Metadata</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <span className="text-muted-foreground">Submitted:</span>{" "}
                {formatRelative(job.submitted_at)}
              </div>
              <div>
                <span className="text-muted-foreground">Duration:</span>{" "}
                {formatDuration(job.started_at, job.finished_at)}
              </div>
              <div>
                <span className="text-muted-foreground">MLflow run:</span>{" "}
                <code>{job.mlflow_run_id ?? "—"}</code>
              </div>
              <div>
                <span className="text-muted-foreground">Failure reason:</span>{" "}
                {job.failure_reason ?? "—"}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Metrics</CardTitle>
            </CardHeader>
            <CardContent>
              <MetricCards metrics={metrics} />
            </CardContent>
          </Card>
          {cm?.labels && cm.matrix && (
            <Card>
              <CardHeader>
                <CardTitle>Confusion matrix</CardTitle>
              </CardHeader>
              <CardContent>
                <ConfusionMatrix labels={cm.labels} matrix={cm.matrix} />
              </CardContent>
            </Card>
          )}
          <Card>
            <CardHeader>
              <CardTitle>Resolved config</CardTitle>
            </CardHeader>
            <CardContent>
              <JsonViewer value={job.resolved_config} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="logs">
          <LogTail text={(logText as string) ?? ""} />
        </TabsContent>

        <TabsContent value="artifacts">
          {job.mlflow_run_id ? (
            <ArtifactTree runId={job.mlflow_run_id} />
          ) : (
            <p className="text-muted-foreground">
              No MLflow run recorded for this job.
            </p>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

- [ ] **Step 2: Wire route in `App.tsx` under `jobs`:**

```tsx
{ path: ":id", lazy: async () => ({
  Component: (await import("./routes/_authed.jobs.$id")).default,
  handle: (await import("./routes/_authed.jobs.$id")).handle,
})},
```

- [ ] **Step 3: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/routes/_authed.jobs.\$id.tsx frontend/src/App.tsx
git commit -m "feat(frontend): job detail (summary + logs + artifacts)"
```

---

## Task 31: Job train E2E

**Files:**

- Create: `frontend/tests/e2e/job-train.spec.ts`

- [ ] **Step 1: Spec**

Write `frontend/tests/e2e/job-train.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("submit train job and see it succeed", async ({ page }) => {
  test.setTimeout(10 * 60_000);
  await login(page);

  await page.goto("/jobs/new");

  // Type (default = train)
  await expect(page.getByRole("button", { name: /^Train$/ })).toHaveAttribute(
    "data-state",
    /active|selected|default/,
  );

  // Pick detector + version (require upxelfdet v0.5.0 built — from Task 22)
  await page
    .getByText(/^Detector$/)
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option", { name: /UPX ELF Detector/i }).click();
  await page
    .getByText(/^Version$/)
    .locator("..")
    .getByRole("combobox")
    .click();
  await page.getByRole("option", { name: /v0\.5\.0/ }).click();

  // Pick datasets (the E2E dataset uploaded in Task 25)
  const datasetPickers = page.locator('[id^="radix"][role="combobox"]');
  await datasetPickers.nth(2).click(); // train dataset
  await page.getByRole("option").first().click();
  await datasetPickers.nth(3).click(); // test dataset
  await page.getByRole("option").first().click();

  // Submit (RJSF form defaults should satisfy schema)
  await page.getByRole("button", { name: /submit job/i }).click();
  await page.waitForURL(/\/jobs\/[0-9a-f-]+/);

  // Wait for succeeded
  await expect(page.getByText(/succeeded/i).first()).toBeVisible({
    timeout: 8 * 60_000,
  });
});
```

- [ ] **Step 2: Run + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm test:e2e job-train
```

Expected: PASS.

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/tests/e2e/job-train.spec.ts
git commit -m "test(frontend): E2E train-job happy path"
```

---

## Task 32: Runs query hooks + experiment list

**Files:**

- Create: `frontend/src/api/queries/runs.ts`
- Create: `frontend/src/routes/_authed.runs._index.tsx`

- [ ] **Step 1: Runs queries**

Write `frontend/src/api/queries/runs.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { client } from "@/api/client";

export const runsKeys = {
  experiments: ["runs", "experiments"] as const,
  experimentRuns: (expId: string) =>
    ["runs", "experiment", expId, "runs"] as const,
  run: (runId: string) => ["runs", "run", runId] as const,
  artifacts: (runId: string, path: string | null) =>
    ["runs", "run", runId, "artifacts", path ?? ""] as const,
};

export function useExperiments() {
  return useQuery({
    queryKey: runsKeys.experiments,
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/experiments");
      if (error) throw error;
      return data as {
        experiment_id: string;
        name: string;
        artifact_location?: string;
      }[];
    },
  });
}

export function useExperimentRuns(expId: string) {
  return useQuery({
    queryKey: runsKeys.experimentRuns(expId),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/experiments/{experiment_id}/runs",
        {
          params: { path: { experiment_id: expId } },
        },
      );
      if (error) throw error;
      return data as {
        run_id: string;
        run_name?: string;
        status: string;
        start_time?: number;
        end_time?: number;
        metrics?: Record<string, number>;
        params?: Record<string, string>;
        tags?: Record<string, string>;
      }[];
    },
    enabled: Boolean(expId),
  });
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: runsKeys.run(runId),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/runs/{run_id}", {
        params: { path: { run_id: runId } },
      });
      if (error) throw error;
      return data;
    },
    enabled: Boolean(runId),
  });
}
```

- [ ] **Step 2: Experiment list route**

Write `frontend/src/routes/_authed.runs._index.tsx`:

```tsx
import { Link } from "react-router";
import { useExperiments } from "@/api/queries/runs";
import { Card, CardContent } from "@/components/ui/card";

export const handle = { breadcrumb: "Runs" };

export default function ExperimentsListPage() {
  const { data, isLoading } = useExperiments();
  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Experiments</h1>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {(data ?? []).map((exp) => (
          <Link key={exp.experiment_id} to={`/runs/${exp.experiment_id}`}>
            <Card className="transition hover:border-primary">
              <CardContent className="p-4">
                <div className="text-xs text-muted-foreground">
                  #{exp.experiment_id}
                </div>
                <div className="text-lg font-medium">{exp.name}</div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/api/queries/runs.ts frontend/src/routes/_authed.runs._index.tsx frontend/src/App.tsx
git commit -m "feat(frontend): experiments list page"
```

---

## Task 33: Runs list (per experiment)

**Files:**

- Create: `frontend/src/routes/_authed.runs.$expId.tsx`

- [ ] **Step 1: Route**

Write `frontend/src/routes/_authed.runs.$expId.tsx`:

```tsx
import { Link, useParams } from "react-router";
import { useExperimentRuns } from "@/api/queries/runs";
import { DataTable } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/common/StatusBadge";
import { formatDuration } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Experiment" };

interface Row {
  run_id: string;
  run_name?: string;
  status: string;
  start_time?: number;
  end_time?: number;
  metrics?: Record<string, number>;
  tags?: Record<string, string>;
}

export default function RunsListPage() {
  const { expId = "" } = useParams();
  const { data, isLoading } = useExperimentRuns(expId);

  const columns: ColumnDef<Row>[] = [
    {
      accessorKey: "run_id",
      header: "Run",
      cell: ({ row }) => (
        <Link
          to={`/runs/${expId}/${row.original.run_id}`}
          className="font-mono text-sm hover:underline"
        >
          {row.original.run_id.slice(0, 10)}
        </Link>
      ),
    },
    { accessorKey: "run_name", header: "Name" },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => (
        <StatusBadge status={row.original.status.toLowerCase()} />
      ),
    },
    {
      id: "duration",
      header: "Duration",
      cell: ({ row }) =>
        row.original.start_time && row.original.end_time
          ? formatDuration(
              new Date(row.original.start_time).toISOString(),
              new Date(row.original.end_time).toISOString(),
            )
          : "—",
    },
    {
      id: "accuracy",
      header: "Accuracy",
      cell: ({ row }) => row.original.metrics?.accuracy?.toFixed(4) ?? "—",
    },
    {
      id: "f1",
      header: "F1",
      cell: ({ row }) =>
        (row.original.metrics?.f1 ?? row.original.metrics?.f1_score)?.toFixed(
          4,
        ) ?? "—",
    },
    {
      id: "job",
      header: "Job",
      cell: ({ row }) => {
        const jobId =
          row.original.tags?.["lolday.job_id"] ??
          row.original.tags?.lolday_job_id;
        return jobId ? (
          <Link to={`/jobs/${jobId}`} className="text-primary hover:underline">
            ↗
          </Link>
        ) : (
          "—"
        );
      },
    },
  ];

  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Runs</h1>
      <DataTable
        data={data ?? []}
        columns={columns}
        emptyMessage="No runs yet."
      />
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/routes/_authed.runs.\$expId.tsx frontend/src/App.tsx
git commit -m "feat(frontend): per-experiment runs list"
```

---

## Task 34: Run detail with confusion matrix

**Files:**

- Create: `frontend/src/routes/_authed.runs.$expId.$runId.tsx`

- [ ] **Step 1: Route**

Write `frontend/src/routes/_authed.runs.$expId.$runId.tsx`:

```tsx
import { useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { useRun } from "@/api/queries/runs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricCards } from "@/components/charts/MetricCards";
import { ConfusionMatrix } from "@/components/charts/ConfusionMatrix";
import { ArtifactTree } from "@/components/common/ArtifactTree";
import { JsonViewer } from "@/components/common/JsonViewer";

export const handle = { breadcrumb: "Run" };

function useConfusionMatrix(runId: string) {
  return useQuery({
    queryKey: ["runs", runId, "cm-artifact"],
    queryFn: async () => {
      try {
        const resp = await fetch(
          `${import.meta.env.VITE_API_BASE}/runs/${runId}/artifacts/download?path=confusion_matrix.json`,
          { credentials: "include" },
        );
        if (!resp.ok) return null;
        return (await resp.json()) as { labels: string[]; matrix: number[][] };
      } catch {
        return null;
      }
    },
    retry: false,
  });
}

export default function RunDetailPage() {
  const { runId = "" } = useParams();
  const { data } = useRun(runId);
  const { data: cm } = useConfusionMatrix(runId);
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  const run = data as unknown as {
    run_id: string;
    status: string;
    start_time?: number;
    end_time?: number;
    metrics?: Record<string, number>;
    params?: Record<string, unknown>;
    tags?: Record<string, string>;
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Run {runId.slice(0, 10)}</h1>
      <Card>
        <CardHeader>
          <CardTitle>Metrics</CardTitle>
        </CardHeader>
        <CardContent>
          <MetricCards metrics={run.metrics ?? {}} />
        </CardContent>
      </Card>
      {cm && (
        <Card>
          <CardHeader>
            <CardTitle>Confusion matrix</CardTitle>
          </CardHeader>
          <CardContent>
            <ConfusionMatrix labels={cm.labels} matrix={cm.matrix} />
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Params</CardTitle>
        </CardHeader>
        <CardContent>
          <JsonViewer value={run.params ?? {}} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Tags</CardTitle>
        </CardHeader>
        <CardContent>
          <JsonViewer value={run.tags ?? {}} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Artifacts</CardTitle>
        </CardHeader>
        <CardContent>
          <ArtifactTree runId={runId} />
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/routes/_authed.runs.\$expId.\$runId.tsx frontend/src/App.tsx
git commit -m "feat(frontend): MLflow run detail (metrics, CM, params, artifacts)"
```

---

## Task 35: Models query hooks (full) + list

**Files:**

- Modify (replace stub from Task 28): `frontend/src/api/queries/models.ts`
- Create: `frontend/src/routes/_authed.models._index.tsx`

- [ ] **Step 1: Replace stub with full model queries (expand Task 28's stub)**

Write `frontend/src/api/queries/models.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { client } from "@/api/client";
import type { components } from "@/api/schema.gen";

export type RegisteredModel = components["schemas"]["RegisteredModelSummary"];
export type ModelVersion = components["schemas"]["ModelVersionRead"];
export type Stage = "Staging" | "Production" | "Archived";

export const modelsKeys = {
  all: ["models"] as const,
  list: () => [...modelsKeys.all, "list"] as const,
  detail: (name: string) => [...modelsKeys.all, "detail", name] as const,
  versions: (name: string) => [...modelsKeys.all, "versions", name] as const,
};

export function useRegisteredModels() {
  return useQuery({
    queryKey: modelsKeys.list(),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models");
      if (error) throw error;
      return data as RegisteredModel[];
    },
  });
}

export function useModelDetail(name: string) {
  return useQuery({
    queryKey: modelsKeys.detail(name),
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models/{name}", {
        params: { path: { name } },
      });
      if (error) throw error;
      return data as RegisteredModel;
    },
    enabled: Boolean(name),
  });
}

export function useModelVersions(name: string) {
  return useQuery({
    queryKey: modelsKeys.versions(name),
    queryFn: async () => {
      const { data, error } = await client.GET(
        "/api/v1/models/{name}/versions",
        {
          params: { path: { name } },
        },
      );
      if (error) throw error;
      return data;
    },
    enabled: Boolean(name),
  });
}

export function useTransitionModel(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: {
      version: number;
      target_stage: Stage;
      comment?: string;
    }) => {
      const { data, error } = await client.POST(
        "/api/v1/models/{name}/versions/{version}/transition",
        {
          params: { path: { name, version: args.version } },
          body: { target_stage: args.target_stage, comment: args.comment },
        },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: modelsKeys.all }),
  });
}
```

- [ ] **Step 2: Models list route**

Write `frontend/src/routes/_authed.models._index.tsx`:

```tsx
import { Link } from "react-router";
import {
  useRegisteredModels,
  type RegisteredModel,
} from "@/api/queries/models";
import { DataTable } from "@/components/tables/DataTable";
import { Badge } from "@/components/ui/badge";
import { formatRelative } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Models" };

const columns: ColumnDef<RegisteredModel>[] = [
  {
    accessorKey: "name",
    header: "Name",
    cell: ({ row }) => (
      <Link
        to={`/models/${encodeURIComponent(row.original.name)}`}
        className="font-medium hover:underline"
      >
        {row.original.name}
      </Link>
    ),
  },
  { accessorKey: "latest_version", header: "Latest version" },
  {
    id: "staging",
    header: "Staging",
    cell: ({ row }) =>
      row.original.staging_version != null ? (
        <Badge variant="secondary">v{row.original.staging_version}</Badge>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    id: "prod",
    header: "Production",
    cell: ({ row }) =>
      row.original.production_version != null ? (
        <Badge className="bg-emerald-600">
          v{row.original.production_version}
        </Badge>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    accessorKey: "last_transitioned_at",
    header: "Last change",
    cell: ({ row }) => formatRelative(row.original.last_transitioned_at),
  },
];

export default function ModelsListPage() {
  const { data, isLoading } = useRegisteredModels();
  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Models</h1>
      <DataTable
        data={data ?? []}
        columns={columns}
        emptyMessage="No models registered yet."
      />
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/api/queries/models.ts frontend/src/routes/_authed.models._index.tsx frontend/src/App.tsx
git commit -m "feat(frontend): models list page"
```

---

## Task 36: Model detail with stage transitions + E2E

**Files:**

- Create: `frontend/src/components/forms/ModelTransitionDialog.tsx`
- Create: `frontend/src/routes/_authed.models.$name.tsx`
- Create: `frontend/tests/e2e/model-transition.spec.ts`

- [ ] **Step 1: ModelTransitionDialog**

Write `frontend/src/components/forms/ModelTransitionDialog.tsx`:

```tsx
import { useState } from "react";
import { useTransitionModel, type Stage } from "@/api/queries/models";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Alert, AlertDescription } from "@/components/ui/alert";

interface Props {
  modelName: string;
  version: number;
  currentStage: Stage;
  hasExistingProd: boolean;
}

export function ModelTransitionDialog({
  modelName,
  version,
  currentStage,
  hasExistingProd,
}: Props) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<Stage>("Production");
  const [comment, setComment] = useState("");
  const mut = useTransitionModel(modelName);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          Transition
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Transition v{version} from {currentStage}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>Target stage</Label>
            <Select value={target} onValueChange={(v) => setTarget(v as Stage)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Staging">Staging</SelectItem>
                <SelectItem value="Production">Production</SelectItem>
                <SelectItem value="Archived">Archived</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Comment (optional)</Label>
            <Textarea
              rows={3}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          </div>
          {target === "Production" && hasExistingProd && (
            <Alert>
              <AlertDescription>
                Another version is currently Production. It will be
                auto-archived when this one is promoted.
              </AlertDescription>
            </Alert>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            disabled={mut.isPending}
            onClick={async () => {
              await mut.mutateAsync({ version, target_stage: target, comment });
              setOpen(false);
            }}
          >
            Confirm
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Model detail route**

Write `frontend/src/routes/_authed.models.$name.tsx`:

```tsx
import { Link, useParams } from "react-router";
import {
  useModelDetail,
  useModelVersions,
  type ModelVersion,
} from "@/api/queries/models";
import { DataTable } from "@/components/tables/DataTable";
import { Badge } from "@/components/ui/badge";
import { ModelTransitionDialog } from "@/components/forms/ModelTransitionDialog";
import { formatRelative } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Model" };

export default function ModelDetailPage() {
  const params = useParams();
  const name = decodeURIComponent(params.name ?? "");
  const { data: model } = useModelDetail(name);
  const { data: versionsData } = useModelVersions(name);
  const versionsArr = (versionsData as { items?: ModelVersion[] })?.items ?? [];
  const existingProd = versionsArr.find(
    (v) => v.current_stage === "Production",
  );

  const columns: ColumnDef<ModelVersion>[] = [
    {
      accessorKey: "mlflow_version",
      header: "Version",
      cell: ({ row }) => `v${row.original.mlflow_version}`,
    },
    {
      accessorKey: "current_stage",
      header: "Stage",
      cell: ({ row }) => <Badge>{row.original.current_stage}</Badge>,
    },
    {
      id: "run",
      header: "Source run",
      cell: ({ row }) => (
        <Link
          to={`/runs/.../${row.original.mlflow_run_id}`}
          className="font-mono text-xs hover:underline"
        >
          {row.original.mlflow_run_id.slice(0, 10)}
        </Link>
      ),
    },
    {
      accessorKey: "created_at",
      header: "Created",
      cell: ({ row }) => formatRelative(row.original.created_at),
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <ModelTransitionDialog
          modelName={name}
          version={row.original.mlflow_version}
          currentStage={
            row.original.current_stage as "Staging" | "Production" | "Archived"
          }
          hasExistingProd={Boolean(
            existingProd &&
            existingProd.mlflow_version !== row.original.mlflow_version,
          )}
        />
      ),
    },
  ];

  if (!model) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">{name}</h1>
      <DataTable
        data={versionsArr}
        columns={columns}
        emptyMessage="No versions registered."
      />
    </div>
  );
}
```

- [ ] **Step 3: E2E**

Write `frontend/tests/e2e/model-transition.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { login } from "./helpers";

test("promote a Staging model to Production", async ({ page }) => {
  await login(page);
  await page.goto("/models");
  const firstModel = page.getByRole("link").first();
  await expect(firstModel).toBeVisible();
  const modelName = await firstModel.textContent();
  await firstModel.click();
  await page.waitForURL(/\/models\//);

  const transitionBtn = page
    .getByRole("button", { name: /transition/i })
    .first();
  await transitionBtn.click();
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: /Production/i }).click();
  await page.getByRole("button", { name: /confirm/i }).click();

  // Expect the row to now show Production badge
  await expect(page.getByText(/Production/).first()).toBeVisible();
});
```

- [ ] **Step 4: Wire + run + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
pnpm test:e2e model-transition
```

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/src/components/forms/ModelTransitionDialog.tsx frontend/src/routes/_authed.models.\$name.tsx frontend/tests/e2e/model-transition.spec.ts frontend/src/App.tsx
git commit -m "feat(frontend): model detail with stage transitions + E2E"
```

---

## Task 37: Dockerfile + nginx.conf

**Files:**

- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`

- [ ] **Step 1: Dockerfile**

Write `frontend/Dockerfile`:

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
# + pid/cache/tmp under /tmp. Works with readOnlyRootFilesystem.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s \
  CMD wget -q --spider http://127.0.0.1:8080/healthz || exit 1
```

- [ ] **Step 2: nginx.conf**

Write `frontend/nginx.conf`:

```nginx
server {
  listen 8080 default_server;
  server_name _;
  root /usr/share/nginx/html;

  # SPA fallback
  location / {
    try_files $uri $uri/ /index.html;
  }

  # Healthcheck for kubelet + Docker HEALTHCHECK
  location = /healthz { return 200 "ok"; }

  # Cache policy: index never cached; hashed assets cached aggressively.
  location = /index.html { add_header Cache-Control "no-store"; }
  location ~* \.(js|css|woff2|svg|png|ico)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
  }

  gzip on;
  gzip_types text/css application/javascript image/svg+xml application/json;

  # Security headers
  add_header X-Content-Type-Options "nosniff"           always;
  add_header X-Frame-Options        "DENY"              always;
  add_header Referrer-Policy        "strict-origin-when-cross-origin" always;
  add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'" always;
}
```

- [ ] **Step 3: Local build sanity check**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
docker build -t lolday-frontend-test:local .
docker run -d --name lolday-fe-test -p 8089:8080 lolday-frontend-test:local
sleep 2
curl -sS http://localhost:8089/healthz
# expect: ok
docker rm -f lolday-fe-test
docker rmi lolday-frontend-test:local
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git add frontend/Dockerfile frontend/nginx.conf
git commit -m "feat(frontend): Dockerfile (nginx-unprivileged) + nginx SPA config"
```

---

## Task 38: Helm chart — frontend Deployment + Service

**Files:**

- Modify: `charts/lolday/values.yaml`
- Create: `charts/lolday/templates/frontend.yaml`

- [ ] **Step 1: Add `frontend` values**

Edit `charts/lolday/values.yaml`. Append:

```yaml
frontend:
  image: harbor.lolday.svc:80/lolday/lolday-frontend:phase5
  host: lolday.islab.local
  replicas: 1
  resources:
    requests: { cpu: 10m, memory: 32Mi }
    limits: { cpu: 100m, memory: 128Mi }
```

- [ ] **Step 2: Frontend Deployment + Service template**

Write `charts/lolday/templates/frontend.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
  namespace: { { .Release.Namespace } }
  labels: { app: frontend }
spec:
  replicas: { { .Values.frontend.replicas } }
  selector: { matchLabels: { app: frontend } }
  template:
    metadata: { labels: { app: frontend } }
    spec:
      containers:
        - name: nginx
          image: { { .Values.frontend.image | quote } }
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8080
          readinessProbe:
            httpGet: { path: /healthz, port: 8080 }
            periodSeconds: 5
          livenessProbe:
            httpGet: { path: /healthz, port: 8080 }
            periodSeconds: 10
          resources: { { - toYaml .Values.frontend.resources | nindent 12 } }
          securityContext:
            runAsNonRoot: true
            readOnlyRootFilesystem: true
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
            seccompProfile:
              type: RuntimeDefault
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
      volumes:
        - { name: tmp, emptyDir: {} }
      imagePullSecrets:
        - name: harbor-pull-cred
---
apiVersion: v1
kind: Service
metadata:
  name: frontend
  namespace: { { .Release.Namespace } }
spec:
  selector: { app: frontend }
  ports:
    - port: 80
      targetPort: 8080
```

- [ ] **Step 3: Helm lint**

```bash
cd /home/bolin8017/Documents/repositories/lolday
helm lint charts/lolday
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add charts/lolday/values.yaml charts/lolday/templates/frontend.yaml
git commit -m "feat(chart): frontend Deployment + Service"
```

---

## Task 39: Traefik IngressRoute

**Files:**

- Create: `charts/lolday/templates/ingress.yaml`

- [ ] **Step 1: Verify Traefik CRD is available**

```bash
kubectl get crd | grep -i traefik
```

Expected: at least `ingressroutes.traefik.io` (Traefik 3.x in K3s 1.34). If empty, K3s shipped without Traefik — abort and ask user to confirm Traefik is installed.

- [ ] **Step 2: IngressRoute template**

Write `charts/lolday/templates/ingress.yaml`:

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: lolday
  namespace: { { .Release.Namespace } }
spec:
  entryPoints: [web]
  routes:
    # Backend API — path prefix must come first so it wins over the catch-all
    - kind: Rule
      match: Host(`{{ .Values.frontend.host }}`) && PathPrefix(`/api/v1`)
      priority: 10
      services:
        - kind: Service
          name: backend
          port: 8000
    # Frontend catch-all
    - kind: Rule
      match: Host(`{{ .Values.frontend.host }}`)
      priority: 1
      services:
        - kind: Service
          name: frontend
          port: 80
```

- [ ] **Step 3: Lint + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
helm lint charts/lolday
git add charts/lolday/templates/ingress.yaml
git commit -m "feat(chart): Traefik IngressRoute routing /api/v1 → backend, / → frontend"
```

---

## Task 40: deploy.sh — accept FRONTEND_IMAGE

**Files:**

- Modify: `scripts/deploy.sh`

- [ ] **Step 1: Add env handle**

Edit `scripts/deploy.sh`. Find the block starting `BACKEND_IMAGE=${BACKEND_IMAGE:-...}` and after it add:

```bash
FRONTEND_IMAGE=${FRONTEND_IMAGE:-harbor.lolday.svc:80/lolday/lolday-frontend:phase5}
```

Then find the `helm upgrade --install lolday ... \` block and add:

```bash
  --set frontend.image="$FRONTEND_IMAGE" \
```

after the existing `--set backend.image=...` line.

- [ ] **Step 2: Dry-run locally**

```bash
cd /home/bolin8017/Documents/repositories/lolday
source ~/.lolday-secrets.env
FRONTEND_IMAGE=harbor.lolday.svc:80/lolday/lolday-frontend:phase5 \
  BACKEND_IMAGE=harbor.lolday.svc:80/lolday/lolday-backend:phase4 \
  bash -n scripts/deploy.sh
```

Expected: exit 0 (syntax OK).

- [ ] **Step 3: Commit**

```bash
git add scripts/deploy.sh
git commit -m "feat(scripts): deploy.sh accepts FRONTEND_IMAGE env"
```

---

## Task 41: Final deploy + full E2E pass + Phase 4 regression

**Files:** none modified — this task builds, deploys, runs all E2E, verifies no regression.

- [ ] **Step 1: Build + push frontend image**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
docker build -t harbor.lolday.svc.cluster.local:80/lolday/lolday-frontend:phase5 .
# Harbor pull creds (same pattern as Phase 3/4):
HARBOR_PUSH_PW=$(kubectl -n lolday get secret harbor-push-cred -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | jq -r '.auths[].auth' | base64 -d | cut -d: -f2)
echo "$HARBOR_PUSH_PW" | docker login harbor.lolday.svc.cluster.local:80 -u 'robot$build-pusher' --password-stdin
docker push harbor.lolday.svc.cluster.local:80/lolday/lolday-frontend:phase5
```

- [ ] **Step 2: Deploy with updated chart + frontend image**

```bash
cd /home/bolin8017/Documents/repositories/lolday
source ~/.lolday-secrets.env
FRONTEND_IMAGE=harbor.lolday.svc:80/lolday/lolday-frontend:phase5 bash scripts/deploy.sh
```

Expected: `Release "lolday" has been upgraded.`

- [ ] **Step 3: Wait for frontend pod**

```bash
kubectl -n lolday rollout status deploy/frontend --timeout=2m
kubectl -n lolday get pods -l app=frontend
```

Expected: `frontend-*   1/1   Running`.

- [ ] **Step 4: Add /etc/hosts entry (if not already)**

Tell user to run (from their laptop / dev machine):

```bash
# Check lab LAN IP for server30 first, e.g. via `ip addr` on server30
echo "<server30-LAN-IP>  lolday.islab.local" | sudo tee -a /etc/hosts
```

- [ ] **Step 5: Browser smoke test**

Open `http://lolday.islab.local/` → redirect to `/login` → login with admin creds → land on `/detectors`. Navigate sidebar, confirm each section loads.

- [ ] **Step 6: Run full E2E suite against the deployed stack**

```bash
cd /home/bolin8017/Documents/repositories/lolday/frontend
export E2E_BASE_URL=http://lolday.islab.local
export E2E_ADMIN_EMAIL=$ADMIN_EMAIL E2E_ADMIN_PASSWORD=$ADMIN_PASSWORD
export E2E_GITHUB_PAT=$(gh auth token)
pnpm test:e2e
```

Expected: 5 specs PASS (login, detector-build, dataset-upload, job-train, model-transition).

- [ ] **Step 7: Phase 4 regression — Bearer token curl still works**

```bash
kubectl -n lolday port-forward svc/backend 8000:8000 &
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=$ADMIN_EMAIL&password=$ADMIN_PASSWORD" \
  -H 'content-type: application/x-www-form-urlencoded' | jq -r .access_token)
test -n "$TOKEN"
# Verify a protected endpoint accepts the Bearer token
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/users/me | jq .email
# expect: admin email
kill %1
```

Expected: bearer token issued; `/users/me` returns admin email.

- [ ] **Step 8: SSH sanity**

```bash
ssh -p 9453 $USER@server30 "uptime && kubectl get pods -n lolday | head -5"
```

Expected: SSH works; pods listed.

- [ ] **Step 9: Update memory file**

```bash
cat >> /home/bolin8017/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/project_lolday_overview.md <<'EOF'

**Phase 5 done (2026-04-XX):** Frontend (React + Vite + shadcn/ui + TanStack Query) deployed as single-replica Deployment `frontend-*` behind Traefik IngressRoute `lolday` at `http://lolday.islab.local/`. Backend added `CookieTransport` under `/api/v1/auth/cookie/*`; bearer transport preserved. 5 Playwright E2E specs pass end-to-end on the deployed stack. Phase 4 curl regression verified.
EOF
```

(Hand-edit date; mention specific commit hash after squash-merge.)

- [ ] **Step 10: Squash-merge phase5-impl → main**

Per Phase 3/4 pattern — user will do this manually:

```bash
cd /home/bolin8017/Documents/repositories/lolday
git checkout main
git merge --squash phase5-impl
git commit -m "feat: phase 5 — frontend"
git push origin main
# Clean up
git branch -D phase5-impl
```

(Done by user; plan ends here.)

---

## Success Criteria (recap)

Phase 5 is complete when:

1. `http://lolday.islab.local/` loads the SPA from the lab LAN.
2. All 5 Playwright specs pass against the deployed stack (`login`, `detector-build`, `dataset-upload`, `job-train`, `model-transition`).
3. Phase 4 curl E2E (Bearer flow) still passes — no backend regression.
4. `ssh -p 9453 server30` works throughout deployment.
5. `kubectl -n lolday get pods` shows `frontend-*` `Running 1/1` alongside all prior Phase 4 pods.
6. `phase5-impl` squash-merged to `main`; spec + plan docs committed under `docs/superpowers/specs/` and `docs/superpowers/plans/`.

---

## Notes for the implementer

- **Commits are per task.** Don't batch. Keep PRs/branches clean so bisecting works.
- **Regenerate API types** (`pnpm run gen-api-types`) after Task 6 (cookie auth adds new paths) and after any backend change.
- **Playwright is sequential** by design — these tests share backend state (the single lab cluster). Don't enable `fullyParallel: true` for this project.
- **E2E state hygiene:** tests create rows (datasets, jobs) with timestamped names to avoid collisions. If you want clean slate, truncate `dataset_config` and `job` tables between runs — but that's a dev-only convenience, not required.
- **RJSF styling ambiguity** is Open Question #1 in the spec. If `rjsf-tailwind` is rough, budget 0.5 day to hand-theme a few base widgets; don't let this block Task 28.
- **`Secure` cookie in dev:** dev uses HTTP (`http://localhost:5173`). Set `COOKIE_SECURE=false` in the backend's dev env before Task 6 E2E, or tests will see no cookie. Production / deployed cluster sets `true`.
- **Type drift** between frontend `schema.gen.ts` and backend: if you add a field to a backend Pydantic model, re-run `pnpm run gen-api-types` and fix any type errors that surface. Don't hand-edit `schema.gen.ts`.
- **shadcn/ui component names** may drift. If `pnpm dlx shadcn add <name>` can't find a component, check the latest names at https://ui.shadcn.com/docs/components.
