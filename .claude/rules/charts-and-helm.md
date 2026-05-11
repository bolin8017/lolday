---
paths:
  - "charts/**/*.{yaml,yml,tpl,json}"
  - "charts/**/Chart.lock"
---

# Helm chart rules (umbrella + sub-charts + helpers)

## Umbrella structure

- `charts/lolday/Chart.yaml` is the umbrella chart.
- `charts/lolday/values.yaml` (~27KB) is the single source of truth for configuration. There is no dev/prod overlay today (tracked tech debt).
- `Chart.yaml.appVersion` follows semver and tracks `Chart.yaml.version` by default (both currently `0.15.0`). Bump them together on releases. The phase-named appVersion convention (`"phase12"`, `"phase13b"`) was retired on 2026-04-29; see `docs/conventions.md` §4.
- Sub-charts are vendored as `charts/lolday/charts/*.tgz`:
  - `harbor 1.18.3` — image registry
  - `kube-prometheus-stack ~84.3.0` — aliased `kps`; provides Prom + Grafana + Alertmanager
  - `loki ~7.0.0` — log aggregation
  - `alloy ~1.8.0` — log/metric agent
  - `trivy-operator ~0.32.1` — image vuln scan
  - `volcano ~1.14.1` — GPU batch queue (core: vcjob is how lolday runs jobs)

## Top-level templates (`charts/lolday/templates/`)

- `backend.yaml`, `frontend.yaml` — Deployment + Service.
- `postgresql.yaml`, `redis.yaml`, `mlflow.yaml`, `registry.yaml`.
- `cloudflared.yaml`, `ingress.yaml` — Cloudflare tunnel + Traefik.
- `alembic-upgrade-hook.yaml` — Helm `pre-upgrade` Job. Runs alembic migrations before the new backend pod starts. Backend boot fails fast if this hook didn't reach `head`.
- `volcano-queue.yaml` — fallback queue `lolday-training` (capability cap matches per-user queues). Per-user queues `lolday-u-<id12>` are created lazily by `backend/app/services/k8s.ensure_user_queue` on first POST /jobs — they are NOT in the chart (cluster-scoped, user lifecycle ≠ chart lifecycle). Spec: `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` §6.3.
- `samples-pv.yaml`, `samples-pvc.yaml` — sample dataset PV/PVC.
- Secrets: `backend-fernet-secret.yaml`, `cloudflared-secret.yaml`, `harbor-admin-secret.yaml`, `mlflow-secret.yaml`.
- NetworkPolicies: `network-policy.yaml`, `netpol-cloudflared.yaml`, `build-networkpolicy.yaml`, `job-networkpolicy.yaml`.
- **Phase 1 (lolday-jobs ns family, since 2026-05-05)** — `jobs-namespace.yaml`, `jobs-quota.yaml`, `jobs-limitrange.yaml`, `jobs-rbac.yaml`, `lolday-quota.yaml`. Detector vcjobs + BuildKit Jobs run in the dedicated `lolday-jobs` namespace so per-namespace `ResourceQuota` / `LimitRange` can cap workload pods without constraining infra. Backend SA in `lolday` has a second Role `backend-jobs` in `lolday-jobs` (preserve Phase 7.5 narrow-scope pattern, do not widen to ClusterRole). NetworkPolicies use cross-ns `namespaceSelector` with `kubernetes.io/metadata.name: lolday`. Spec: `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` §6.2.

## `templates/monitoring/` subfolder

- `alertmanager-rules.yaml` + `alertmanager-config-discord.yaml` — alerting rules + Discord receivers + 5 inhibition rules + per-severity routing. 16 alert rules total (alerting redesign 2026-05-10). Receivers wire to two distinct Discord channels via Secret keys `webhook-url-critical` (Captain Hook, @here) and `webhook-url-warning` (Spidey Warnings, no @here). See `docs/superpowers/specs/2026-05-10-alerting-redesign-design.md`.
- `deadmans-switch.yaml` — CronJob that posts to a Discord webhook on a schedule. Uses an **independent** env var `DISCORD_URL`, **distinct** from the backend's `DISCORD_WEBHOOK_URL_EVENTS`. Missing `DISCORD_URL` causes fail-fast (RuntimeError) — by design (see `charts/lolday/files/deadmans_switch/check.py`).
- `grafana-admin-secret.yaml`, `grafana-dashboards.yaml` — Grafana wiring.
- `namespace.yaml` — monitoring namespace.
- `postgres-exporter-initjob.yaml` + `postgres-exporter.yaml` — Postgres metrics exporter.
- `servicemonitor-{backend,dcgm,postgres,traefik,trivy,volcano}.yaml` — six ServiceMonitor resources.

## Helper images (`charts/lolday/helpers/`)

Four helpers, two release flows.

### Content-addressable (managed by `scripts/build-helpers.sh`)

- `build-helper/` — Python tool. Includes `maldet_validator.py` which asserts a built detector matches the maldet spec. Has its own `pyproject.toml` + `uv.lock` + `test_maldet_validator.py`.
- `job-helper/` — Python module + tests + `uv.lock`. The vcjob init / sidecar / model-fetcher container.

Tags are 12-char subtree SHAs derived from `git rev-parse HEAD:charts/lolday/helpers/<name>`. They are pinned in `charts/lolday/helpers.lock` (JSON, git-tracked) and injected at deploy time via `scripts/deploy.sh --set backend.env.BUILD_IMAGE_HELPER=... --set backend.env.JOB_HELPER_IMAGE=...`.

`backend/app/config.py` has empty defaults for both env vars and a `validate_helper_images` model_validator that fails boot in production when either is unset. The pre-commit hook `helpers-lock-fresh` blocks commits that leave the lock out of sync with the helper subtrees.

Operator flow → `docs/runbooks/release-helpers.md`. Spec → `docs/superpowers/specs/2026-04-29-helper-image-versioning-design.md`.

### Manually pinned (semantic tags)

- `mlflow-server/` — Dockerfile only; produces the custom MLflow image. Tag = upstream MLflow version, e.g. `:v2.20.3`.
- `pytorch-cu12-base/` — Dockerfile only; GPU base image. Tag = `<torch>-<cuda>` set, e.g. `:2.7.0-cu126`.

These do not flow through `helpers.lock`; their tags carry external semantic meaning that subtree SHA strips. Bumping them is a manual edit to the relevant `values.yaml` line + a `docker build` + `docker push` from the operator's host.

## Dashboards (`charts/lolday/dashboards/`)

JSON dashboards mounted by `monitoring/grafana-dashboards.yaml`:

- `dcgm.json` — GPU metrics
- `postgresql.json` — DB metrics
- `reconciler-errors.json` — `BACKEND_ERRORS{stage=...}` breakdown
- `traefik.json` — ingress metrics
- `trivy-security.json` — vuln-scan results

## Workflow

```bash
helm lint charts/lolday
helm template charts/lolday > /tmp/out.yaml         # inspect rendered diff
helm dependency update charts/lolday                # re-fetches *.tgz under charts/lolday/charts/
```

Never commit the sub-chart `*.tgz` files. They are listed in `.gitignore` and re-fetched on demand.

## NetworkPolicy changes

Read the SSH safety hard rule in root `CLAUDE.md` first. Any change that could affect host iptables on server30 must be dry-runnable, and the operator must verify SSH from a fresh session before applying.

## values.yaml hygiene

- Single file today; no overlay system.
- Secrets go through `*-secret.yaml` templates wired to external sources (operator-local `.lolday-secrets.env` or in-cluster Secrets). Never put plaintext credentials in `values.yaml`.

## CI

`helm dependency update`, `helm lint`, `helm template` enforced by `.github/workflows/helm.yml`. Helper image Dockerfile build verification (build-helper, job-helper only) by `.github/workflows/helpers.yml` — `mlflow-server` and `pytorch-cu12-base` are excluded by design (operator manual). Discipline rules in `.claude/rules/github-actions.md`.
