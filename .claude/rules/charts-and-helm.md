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
- `volcano-queue.yaml` — Volcano queue for GPU jobs.
- `samples-pv.yaml`, `samples-pvc.yaml` — sample dataset PV/PVC.
- Secrets: `backend-fernet-secret.yaml`, `cloudflared-secret.yaml`, `harbor-admin-secret.yaml`, `mlflow-secret.yaml`.
- NetworkPolicies: `network-policy.yaml`, `netpol-cloudflared.yaml`, `build-networkpolicy.yaml`, `job-networkpolicy.yaml`.

## `templates/monitoring/` subfolder

- `alertmanager-rules.yaml` + `alertmanager-config-discord.yaml` — alerting rules + Discord receiver.
- `deadmans-switch.yaml` — CronJob that posts to a Discord webhook on a schedule. Uses an **independent** env var `DISCORD_URL`, **distinct** from the backend's `DISCORD_WEBHOOK_URL_EVENTS`. Missing `DISCORD_URL` causes fail-fast (RuntimeError) — by design (see `charts/lolday/files/deadmans_switch/check.py`).
- `grafana-admin-secret.yaml`, `grafana-dashboards.yaml` — Grafana wiring.
- `namespace.yaml` — monitoring namespace.
- `postgres-exporter-initjob.yaml` + `postgres-exporter.yaml` — Postgres metrics exporter.
- `servicemonitor-{backend,dcgm,postgres,traefik,trivy,volcano}.yaml` — six ServiceMonitor resources.

## Helper images (`charts/lolday/helpers/`)

Each helper has its own Dockerfile, built and pushed manually by the operator.

- `build-helper/` — Python tool. Includes `maldet_validator.py` which asserts a built detector matches the maldet spec. Has its own `pyproject.toml` + `uv.lock` + `test_maldet_validator.py`.
- `job-helper/` — Python module + tests + `uv.lock`. This is the entrypoint inside vcjob containers.
- `mlflow-server/` — Dockerfile only; produces a custom mlflow image.
- `pytorch-cu12-base/` — Dockerfile only; GPU base image.

Image tags are hardcoded in `backend/app/config.py`:

- `BUILD_IMAGE_HELPER` defaults to `harbor.harbor.svc:80/lolday/build-helper:v3`
- `JOB_HELPER_IMAGE` defaults to `harbor.lolday.svc:80/lolday/job-helper:v4` (note: Harbor URL inconsistency — see `docs/architecture.md` §9)

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
