# Lolday — internal ML platform for ISLab malware detector management

@README.md

## Lab / infrastructure context

Deploy target: **server30** (`140.118.155.30`, SSH port 9453). Ubuntu 24.04, shared lab server, K3s single-node.

### SSH safety — never break SSH on server30

A broken SSH causes "重大的損失" (major loss — no physical access fallback). On 2026-03-31 a Cilium CNI install broke SSH and required significant recovery effort. See the Cilium incident post-mortem in auto memory (`project_cilium_ssh_incident.md`) before any CNI / network rework.

- Before any network / firewall / iptables / CNI / sysctl change, verify SSH will not be affected.
- Never modify UFW rules, iptables, or CNI config without checking SSH impact.
- After every infra step, verify SSH is still active.
- For dangerous operations, ask another agent to review first.

### Sudo policy

The user normally has **no sudo** on server30. They grant it temporarily for specific tasks then revoke it.

- Install CLI tools at user level under `~/.local/bin/` (kubectl, helm, cilium, k9s, etc.). Never system-wide when a user-level install is possible.
- For sudo operations, **write the commands / scripts and hand them to the user to run** — do not invoke sudo directly.
- In install / cleanup scripts, use `sudo` only on the specific commands that truly require it.

### Avoid China-origin software

ISLab is a Taiwanese security research lab. Default to English-ecosystem / GitHub-mainstream software; flag China-origin choices for the user to approve.

- Component libraries: prefer **shadcn/ui, MUI, Chakra, Radix**. Avoid Ant Design, Arco (ByteDance), TDesign (Tencent), ElementUI, Naive UI.
- Forms / state / validation: prefer **TanStack, react-hook-form, zod, Redux Toolkit**.
- Cloud / SaaS: prefer **Cloudflare, GitHub, Vercel, Resend**.
- i18n: keep **zh-TW** as first-class, not zh-CN.
- Vite is an accepted gray zone (now Vercel-backed); other build tools fine on request.
- Exception: use a China-origin tool when it has a clear advantage and no reasonable alternative — flag the choice explicitly first.

## Design principle — prefer open-source packages over custom code

Lolday is a glue platform. For every component, **first look for an existing open-source / actively maintained project** before proposing a custom implementation. Write custom code only for the glue layer and `maldet`-spec-specific logic.

## Project layout

- `backend/` — FastAPI + uv. Entry `backend/app/main.py`; routers / services / models / schemas split. Alembic migrations under `backend/migrations/`.
- `frontend/` — Vite + React + TypeScript + Tailwind + shadcn/ui. Entry `frontend/src/main.tsx`. pnpm-managed.
- `charts/lolday/` — Helm chart (templates, dashboards, helpers, sub-charts).
- `scripts/` — install / setup / teardown / diag operational scripts.
- `docs/` — phase E2E checklists, post-mortems, runbooks.
- `docs/superpowers/specs/` & `docs/superpowers/plans/` — Phase-level design + implementation docs (Phase 1–11e).
- `tests/` — repo-level integration tests.

## Build / test commands

### Initial setup

```bash
bash scripts/install-tools.sh    # CLI tools, no sudo → ~/.local/bin/
sudo bash scripts/setup-k3s.sh   # K3s install — give to user to run
bash scripts/deploy.sh           # platform deploy, no sudo
```

### Backend (`backend/`, uv-managed)

```bash
cd backend && uv run pytest
cd backend && uv run alembic upgrade head
```

### Frontend (`frontend/`, pnpm-managed)

```bash
cd frontend && pnpm install
cd frontend && pnpm test                # vitest
cd frontend && pnpm playwright test     # E2E
cd frontend && pnpm build
```

### Helm chart

```bash
helm lint charts/lolday
helm template charts/lolday --values charts/lolday/values.yaml
```

## Phase-level conventions

- New phase work goes in `docs/superpowers/specs/YYYY-MM-DD-phaseN-X-design.md` and `docs/superpowers/plans/YYYY-MM-DD-phaseN-X.md`.
- Detector framework `maldet` is a **separate** PyPI package; lolday consumes it. Phase 11a–11e tracks the integration.
- For accumulated project facts (Phase 9.6 PVC findings, Phase 10 SSO details, Phase 11 progress, ELF detector templates, maldet framework v1, Cilium incident), see auto memory at `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/`.
