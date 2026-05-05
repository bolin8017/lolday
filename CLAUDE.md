# Lolday — internal ML platform for ISLab malware detector management

@README.md

## How to navigate this codebase

- 系統架構 / 模組責任 / 外部服務 / env vars / 技術債 → `docs/architecture.md`
- 部署 / 維運 → `docs/runbooks/deploy.md`、`docs/runbooks/troubleshooting.md`
- MLflow 全清 + gc（一次性 destructive；用於 cutover / 重置） → `docs/runbooks/wipe-mlflow.md`
- 命名 / 分支 / commit / migration 慣例 → `docs/conventions.md`
- Detector repo 清單（cutover / 升 maldet 用） → `docs/detector-repos.md`
- 在 `backend/` / `frontend/` / `charts/` / `scripts/` / `backend/migrations/` 工作 →
  自動載入對應 `.claude/rules/<area>.md`（path-scoped）
- 過去 Phase 紀錄 / E2E checklists → `docs/phase-history/`
- 事故 postmortem → `docs/postmortems/`
- Phase 設計 / 實作計畫 → `docs/superpowers/specs/`、`docs/superpowers/plans/`
- Backend FIFO scheduler (Phase 6) → `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md`、`docs/runbooks/admin-priority.md`

## Hard rules（每個 session 都必須記得）

### SSH safety on server30

A broken SSH causes 重大的損失 — server30 has no IPMI / out-of-band fallback. On 2026-03-31 a Cilium CNI install broke SSH and required physical recovery (see `docs/postmortems/2026-03-31-cilium-ssh-incident.md`).

- Before any network / firewall / iptables / UFW / CNI / sysctl change, verify SSH will not be affected.
- Never modify UFW rules, iptables, or CNI config without dry-running and prompting the operator to verify SSH in a fresh session.
- After every infra step, verify SSH is still active.
- For dangerous operations, ask another agent to review first.

### Sudo policy

The operator normally has **no sudo** on server30. Sudo is granted temporarily and then revoked.

- Install CLI tools at user level under `~/.local/bin/` (kubectl, helm, k9s, cilium, etc.). Never system-wide when a user-level install is possible.
- For sudo operations, **write the commands / scripts and hand them to the operator** — do not invoke sudo directly.
- In install / cleanup scripts, use `sudo` only on the specific lines that truly require it; comment them `# requires sudo`.

### Avoid China-origin software

ISLab is a Taiwanese security research lab. Default to English-ecosystem / GitHub-mainstream software; flag China-origin choices for the operator to approve.

- Component libraries: prefer **shadcn/ui, MUI, Chakra, Radix**. Avoid Ant Design, Arco (ByteDance), TDesign (Tencent), ElementUI, Naive UI.
- Forms / state / validation: prefer **TanStack, react-hook-form, zod, Redux Toolkit**.
- Cloud / SaaS: prefer **Cloudflare, GitHub, Vercel, Resend**.
- i18n: keep **zh-TW** as first-class, not zh-CN.
- Vite is an accepted gray zone (now Vercel-backed).
- Exception: use a China-origin tool when it has a clear advantage and no reasonable alternative — flag it explicitly first.

### Lint / format 不繞過

紀律由 `pre-commit` 自動套用。任何形式的 bypass 都是破壞紀律。

- `git commit --no-verify` 視同破壞紀律。Hook 失敗請查 root cause，不要 bypass。
- 任何 `# noqa: <code>` / `# type: ignore[<code>]` 必須在同一行附 reason 註解。
- `# fmt: off` / `# fmt: on` 區段是 ruff 官方支援的「此處刻意保留 layout」標記，可用，但要附理由（若意圖非顯而易見）。

### Prefer open-source packages over custom code

Lolday is a glue platform. For every component, **first look for an existing open-source / actively maintained project** before proposing a custom implementation. Write custom code only for the glue layer and `maldet`-spec-specific logic.

## Quickstart commands

```bash
bash scripts/install-tools.sh           # CLI tools, no sudo → ~/.local/bin/
sudo bash scripts/setup-k3s.sh          # K3s install — give to sudo-capable account
sudo bash scripts/patch-k3s-kubelet-args.sh  # host safety reservations on existing K3s
bash scripts/deploy.sh                  # platform deploy, no sudo
bash scripts/build-helpers.sh           # build + push helper images, refresh helpers.lock
cd backend && uv run pytest             # backend tests
cd frontend && pnpm test                # frontend unit (vitest)
cd frontend && pnpm playwright test     # frontend E2E
helm lint charts/lolday                 # helm sanity
pre-commit run --all-files              # lint+format whole repo (also auto-runs on git commit)
gh workflow run lint.yml                # trigger CI sanity from local (needs gh CLI)
```

Detailed flow → `docs/runbooks/deploy.md` and `docs/architecture.md` §6.

## Project layout

| Path                                                                  | What                                 | Detailed rules                        |
| --------------------------------------------------------------------- | ------------------------------------ | ------------------------------------- |
| `backend/`                                                            | FastAPI + uv                         | `.claude/rules/backend.md`            |
| `frontend/`                                                           | Vite + React + TS                    | `.claude/rules/frontend.md`           |
| `charts/lolday/`                                                      | Helm umbrella + sub-charts + helpers | `.claude/rules/charts-and-helm.md`    |
| `scripts/`                                                            | install / deploy / diag / recover    | `.claude/rules/scripts-and-ops.md`    |
| `backend/migrations/`                                                 | Alembic                              | `.claude/rules/alembic-migrations.md` |
| `tests/phase7/`                                                       | shell-based smoke tests              | —                                     |
| `docs/superpowers/{specs,plans}/`                                     | Phase planning artefacts             | `docs/conventions.md`                 |
| `docs/{architecture,conventions,runbooks,phase-history,postmortems}/` | platform docs                        | this file                             |
