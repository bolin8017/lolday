# Lolday — internal ML platform for ISLab malware detector management

@README.md
@docs/operations.md

## What this is (TL;DR)

Lolday is the K3s-on-server30 runtime that builds, schedules, and serves ISLab's malware detectors. Researchers tune detectors in their own repos (`elfrfdet`, `elfcnndet`, …), tag a release, and lolday handles **build → Harbor push → vcjob scheduling → MLflow tracking → user notification**. The framework detectors import is `maldet` (PyPI). Authoritative detector inventory: `docs/detector-repos.md`.

Core stack: FastAPI backend + Vite/React frontend + PostgreSQL + Redis + MLflow + Harbor (OCI registry) + Volcano (GPU batch queue) + kube-prometheus-stack + Loki + Cloudflare Access SSO. **MinIO is the unified S3 backend** for MLflow artifacts / Harbor blobs / Loki chunks (since spec 2026-05-11). System diagram + per-component table: `docs/architecture.md` §2-3.

Day-to-day operator data (Discord channel IDs, `.env` files, server access): `docs/operations.md` — already loaded via the `@import` above.

## How to navigate this codebase

- 系統架構 / 模組責任 / 外部服務 / env vars / 技術債 → `docs/architecture.md`
- 部署 / 維運 → `docs/runbooks/deploy.md`、`docs/runbooks/troubleshooting.md`
- MLflow 全清 + gc（一次性 destructive；用於 cutover / 重置） → `docs/runbooks/wipe-mlflow.md` ⚠️ pre-MinIO，待重寫
- 命名 / 分支 / commit / migration 慣例 → `docs/conventions.md`
- Detector repo 清單（cutover / 升 maldet 用） → `docs/detector-repos.md`
- 在 `backend/` / `frontend/` / `charts/` / `scripts/` / `backend/migrations/` 工作 →
  自動載入對應 `.claude/rules/<area>.md`（path-scoped）
- 過去 Phase 紀錄 / E2E checklists → `docs/phase-history/`
- 事故 postmortem → `docs/postmortems/`
- Phase 設計 / 實作計畫 → `docs/superpowers/specs/`、`docs/superpowers/plans/`
- Backend FIFO scheduler (Phase 6) → `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md`、`docs/runbooks/admin-priority.md`
- Host-aware GPU signal (DCGM + Prom + scheduler) → `docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md`、`backend/app/services/gpu_signal.py`
- MLflow data-model redesign (2026-05-11) → `docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md`、`backend/app/services/mlflow_client.py`、`backend/app/reconciler/jobs.py::_finalize_mlflow_run`
- 儲存層架構 / SSD 擴充 / object vs block 分層 → `docs/architecture.md` §6、`docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md`(spec 寫的 endpoint `minio.lolday.svc:9000` 實作後修正為 `lolday-minio.lolday.svc:9000`)
- 加新 SSD 的 step-by-step → `docs/runbooks/add-ssd.md` ⚠️ MinIO chart 限制，需重新設計
- 一次性 storage migration (filesystem→S3) → `docs/runbooks/storage-migration.md`

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

### Deploy platform, not development platform

Lolday is the runtime for **already-tuned** detectors. Authors finish all hyperparameter tuning, threshold selection, and calibration in their own repos before tagging a release. The platform must NOT expose UI knobs that let platform users override detector-author design decisions.

**Stage-aware rule**: `TrainConfig` may have user-tunable hparams (per-experiment); `EvaluateConfig` / `PredictConfig` may have only resource / perf knobs (no behavioral knobs).

Precedents (footgun removals):

- PR #112 (2026-05-07) — detector-version override toggle
- 2026-05-08 spec — `EvaluateConfig.threshold` field

Full reasoning: `docs/architecture.md` §1.2 + §1.3.

### 儲存層僅透過 MinIO，不要回退 filesystem

MLflow artifact、Harbor blob、Loki chunk 在 spec `2026-05-11-storage-architecture-redesign-design.md` 落地後**全部**走 MinIO S3 backend。在這些元件的 chart / values 改動裡：

- 不要再加 PVC mount 到 `/mlflow-artifacts`、`/storage`(Harbor registry)、`/var/loki/chunks`
- 不要在 Helm values 把 storage type 改回 `filesystem`
- 若有「先暫存到本地、稍後上傳」需求，用 MinIO 的 presigned URL 或 multipart upload，不要繞回 PVC

回退會破壞：統一 retention 策略、SSD 擴充流程、未來 multi-node 升級路徑。

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
