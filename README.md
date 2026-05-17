# Lolday

[![lint](https://github.com/bolin8017/lolday/actions/workflows/lint.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/lint.yml)
[![backend](https://github.com/bolin8017/lolday/actions/workflows/backend.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/backend.yml)
[![frontend](https://github.com/bolin8017/lolday/actions/workflows/frontend.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/frontend.yml)
[![helm](https://github.com/bolin8017/lolday/actions/workflows/helm.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/helm.yml)
[![images](https://github.com/bolin8017/lolday/actions/workflows/images.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/images.yml)
[![helpers](https://github.com/bolin8017/lolday/actions/workflows/helpers.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/helpers.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Internal ML platform for ISLab's malware detector lifecycle — build, schedule, track, deliver.

## What it is

Lolday is the K3s-on-server30 runtime that builds, schedules, and serves ISLab's
malware detectors. Researchers tune detectors in their own repos (`elfrfdet`,
`elfcnndet`, …), tag a release, and lolday handles **build → Harbor push →
vcjob scheduling → MLflow tracking → user notification**. The framework
detectors import is [`maldet`](https://github.com/bolin8017/maldet) (PyPI).

Core stack: FastAPI backend + Vite/React frontend + PostgreSQL + Redis + MLflow +
Harbor (OCI registry) + Volcano (GPU batch queue) + kube-prometheus-stack +
Loki + Cloudflare Access SSO. **MinIO is the unified S3 backend** for MLflow
artifacts / Harbor blobs / Loki chunks (since spec 2026-05-11).

Lolday is a **deploy platform, not a development platform** — it runs
already-tuned detectors. Hyperparameter sweeps, threshold selection, and
calibration belong in the detector authors' own repos. See
[`docs/architecture.md` §1.2](docs/architecture.md).

## Quick start

```bash
bash scripts/install-tools.sh              # CLI tools (incl. cosign), no sudo → ~/.local/bin/
sudo bash scripts/setup-k3s.sh             # K3s install, requires sudo
helm install gpu-operator nvidia/gpu-operator -n gpu-operator --create-namespace \
  --set driver.enabled=false --set toolkit.enabled=true \
  --set devicePlugin.enabled=true --set dcgmExporter.enabled=true \
  --wait --timeout 5m
bash scripts/deploy.sh                     # first round; backend CrashLoopBackOff until helpers pushed
bash scripts/recover-harbor.sh             # Harbor projects + robot account
bash scripts/cosign-harbor-init.sh         # one-time: cosign keypair + Kyverno pubkey Secret
bash scripts/build-helpers.sh              # build + push (signed) helper images; refresh helpers.lock
bash scripts/deploy.sh                     # second round; backend clean
```

The K3s API audit log + `--secrets-encryption` flags are operator-applied
to an existing cluster via
**[`docs/runbooks/k3s-audit-and-secrets-encryption.md`](docs/runbooks/k3s-audit-and-secrets-encryption.md)**.
Fresh installs ship with the flags baked into `scripts/setup-k3s.sh`.

Full procedure with pre-requisites, sysctls, verification, rollback, and SSH
safety steps: **[docs/runbooks/deploy.md](docs/runbooks/deploy.md)**.

Teardown:

```bash
bash scripts/teardown.sh
```

## Documentation

Start here:

- **[Architecture](docs/architecture.md)** — components, data flows, env vars, tech debt, gotchas
- **[Operations quick reference](docs/operations.md)** — Discord channels, `.env` files, server access
- **[Deploy runbook](docs/runbooks/deploy.md)** — pre-requisites, K3s, GPU operator, Helm, verify, rollback
- **[Troubleshooting](docs/runbooks/troubleshooting.md)** — symptom → action lookup
- **[Conventions](docs/conventions.md)** — branch / commit / PR / spec naming / CI

Runbooks for specific operations:

- [Admin priority bump](docs/runbooks/admin-priority.md)
- [Adding an SSD](docs/runbooks/add-ssd.md) (invalidated; see warning at top)
- [Adding a new NFS-backed sample bank](docs/runbooks/add-nfs-dataset.md)
- [Cloudflare Access backups](docs/runbooks/cf-access-backups.md)
- [Discord webhook rotation](docs/runbooks/discord-webhook-rotation.md)
- [Fernet key rotation](docs/runbooks/p3-fernet-rotation.md)
- [K3s API audit log + secrets encryption](docs/runbooks/k3s-audit-and-secrets-encryption.md)
- [Kyverno Harbor image signing](docs/runbooks/kyverno-harbor-signing.md)
- [Operator workstation backup](docs/runbooks/operator-workstation-backup.md)
- [Orphan job-token Secrets cleanup](docs/runbooks/orphan-job-tokens-cleanup.md)
- [PostgreSQL restore from MinIO backup](docs/runbooks/db-restore.md)
- [PSS label promotion](docs/runbooks/pss-label-promotion.md)
- [Releasing helper images](docs/runbooks/release-helpers.md)
- [Storage migration](docs/runbooks/storage-migration.md) (one-time, historical)
- [Wiping MLflow](docs/runbooks/wipe-mlflow.md) (pre-MinIO; see warning at top)

Security:

- [Security policy + vulnerability reporting](SECURITY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

Audit trail (immutable):

- [Specs](docs/superpowers/specs/) — per-topic design docs (`YYYY-MM-DD-<topic>-design.md`)
- [Plans](docs/superpowers/plans/) — per-topic implementation plans
- [Phase history](docs/phase-history/) — past E2E checklists, retirement findings
- [Postmortems](docs/postmortems/) — incident write-ups

Originals (traceability):

- [Original platform design spec](docs/superpowers/specs/2026-03-30-lolday-platform-design.md)
- [Phase 1 plan](docs/superpowers/plans/2026-03-30-phase1-infrastructure.md)

## Contributing

Internal ISLab platform. Branch / commit / PR conventions, path-scoped Claude
rules, testing commands: **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## License

[Apache-2.0](LICENSE).
