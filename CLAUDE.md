# Lolday — internal ML platform for ISLab malware detector management

@README.md
@docs/operations.md

## What this is (TL;DR)

Lolday is the K3s-on-server30 runtime that builds, schedules, and serves ISLab's malware detectors. Researchers tune detectors in their own repos (`elfrfdet`, `elfcnndet`, …), tag a release, and lolday handles **build → Harbor push → vcjob scheduling → MLflow tracking → user notification**. The framework detectors import is `maldet` (PyPI). Authoritative detector inventory: `docs/detector-repos.md`.

Core stack: FastAPI backend + Vite/React frontend + PostgreSQL + Redis + MLflow + Harbor (OCI registry) + Volcano (GPU batch queue) + kube-prometheus-stack + Loki + Cloudflare Access SSO. **MinIO is the unified S3 backend** for MLflow artifacts / Harbor blobs / Loki chunks (since spec 2026-05-11). System diagram + per-component table: `docs/architecture.md` §2-3.

Day-to-day operator data (Discord channel IDs, `.env` files, server access): `docs/operations.md` — already loaded via the `@import` above.

## How to navigate this codebase

- System architecture / module responsibilities / external services / env vars / tech debt → `docs/architecture.md`
- Deploy / operations → `docs/runbooks/deploy.md`, `docs/runbooks/troubleshooting.md`
- MLflow full wipe + gc (one-time destructive; for cutover / reset) → `docs/runbooks/wipe-mlflow.md` ⚠️ pre-MinIO, pending rewrite
- Naming / branch / commit / migration conventions → `docs/conventions.md`
- Detector repo inventory (cutover / maldet bump) → `docs/detector-repos.md`
- Working under `backend/` / `frontend/` / `charts/` / `scripts/` / `backend/migrations/` →
  the matching `.claude/rules/<area>.md` loads automatically (path-scoped)
- Past phase records / E2E checklists → `docs/phase-history/`
- Incident postmortems → `docs/postmortems/`
- Phase designs / implementation plans → `docs/superpowers/specs/`, `docs/superpowers/plans/`
- Backend FIFO scheduler (Phase 6) → `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md`, `docs/runbooks/admin-priority.md`
- Host-aware GPU signal (DCGM + Prom + scheduler) → `docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md`, `backend/app/services/gpu_signal.py`
- MLflow data-model redesign (2026-05-11) → `docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md`, `backend/app/services/mlflow_client.py`, `backend/app/reconciler/jobs.py::_finalize_mlflow_run`
- Storage architecture / SSD expansion / object-vs-block layering → `docs/architecture.md` §6, `docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md` (spec wrote endpoint `minio.lolday.svc:9000`; corrected to `lolday-minio.lolday.svc:9000` during implementation)
- Step-by-step for adding an SSD → `docs/runbooks/add-ssd.md` ⚠️ MinIO chart limitation, needs redesign
- One-time storage migration (filesystem → S3) → `docs/runbooks/storage-migration.md`
- NFS dataset union mount onboarding (since 2026-05-12) → `docs/runbooks/add-nfs-dataset.md`
- NFS dataset union mount design (mergerfs over NFSv4.2) → `docs/superpowers/specs/2026-05-12-nfs-dataset-union-mount-design.md`

## Hard rules (every session must remember)

### SSH safety on server30

A broken SSH causes severe loss — server30 has no IPMI / out-of-band fallback. On 2026-03-31 a Cilium CNI install broke SSH and required physical recovery (see `docs/postmortems/2026-03-31-cilium-ssh-incident.md`).

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

### Lint / format: no bypass

Discipline is enforced by `pre-commit`. Any form of bypass breaks the discipline.

- `git commit --no-verify` is treated as a bypass. If a hook fails, find the root cause; do not bypass.
- Any `# noqa: <code>` / `# type: ignore[<code>]` must include an inline reason comment on the same line.
- `# fmt: off` / `# fmt: on` blocks are the ruff-supported markers for intentionally preserving layout; use them but add a reason when the intent is not self-evident.

### Prefer open-source packages over custom code

Lolday is a glue platform. For every component, **first look for an existing open-source / actively maintained project** before proposing a custom implementation. Write custom code only for the glue layer and `maldet`-spec-specific logic.

### Deploy platform, not development platform

Lolday is the runtime for **already-tuned** detectors. Authors finish all hyperparameter tuning, threshold selection, and calibration in their own repos before tagging a release. The platform must NOT expose UI knobs that let platform users override detector-author design decisions.

**Stage-aware rule**: `TrainConfig` may have user-tunable hparams (per-experiment); `EvaluateConfig` / `PredictConfig` may have only resource / perf knobs (no behavioral knobs).

Precedents (footgun removals):

- PR #112 (2026-05-07) — detector-version override toggle
- 2026-05-08 spec — `EvaluateConfig.threshold` field

Full reasoning: `docs/architecture.md` §1.2 + §1.3.

### Storage layer goes through MinIO only — do not fall back to filesystem

After spec `2026-05-11-storage-architecture-redesign-design.md` landed, MLflow artifacts, Harbor blobs, and Loki chunks **all** go through the MinIO S3 backend. In chart / values changes for these components:

- Do not add PVC mounts to `/mlflow-artifacts`, `/storage` (Harbor registry), or `/var/loki/chunks`
- Do not flip Helm values storage type back to `filesystem`
- If you need a "stage locally, upload later" path, use MinIO presigned URLs or multipart upload — do not loop back through a PVC

Reverting breaks: the unified retention policy, the SSD expansion workflow, and the future multi-node upgrade path.

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
