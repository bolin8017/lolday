# Lolday

[![lint](https://github.com/bolin8017/lolday/actions/workflows/lint.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/lint.yml)
[![backend](https://github.com/bolin8017/lolday/actions/workflows/backend.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/backend.yml)
[![frontend](https://github.com/bolin8017/lolday/actions/workflows/frontend.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/frontend.yml)
[![helm](https://github.com/bolin8017/lolday/actions/workflows/helm.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/helm.yml)
[![images](https://github.com/bolin8017/lolday/actions/workflows/images.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/images.yml)
[![helpers](https://github.com/bolin8017/lolday/actions/workflows/helpers.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/helpers.yml)

Internal ML platform for ISLab malware detector management.

## Prerequisites

- NVIDIA drivers installed on host (`nvidia-smi` must work)
- Temporary sudo access for K3s installation

## Setup

```bash
# 1. Install CLI tools (no sudo)
bash scripts/install-tools.sh

# 2. Install K3s (requires sudo — run with a sudo-capable account)
sudo bash scripts/setup-k3s.sh

# 3. Install GPU Operator (no sudo)
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
helm install gpu-operator nvidia/gpu-operator \
  -n gpu-operator --create-namespace \
  --set driver.enabled=false \
  --set toolkit.enabled=true \
  --set devicePlugin.enabled=true \
  --set dcgmExporter.enabled=true \
  --wait --timeout 5m

# 4. Deploy the platform — first round (no sudo)
#    Brings up Harbor + monitoring; backend will CrashLoopBackOff until
#    helper images are pushed in step 6.
bash scripts/deploy.sh

# 5. Bootstrap Harbor projects + robot account
bash scripts/recover-harbor.sh

# 6. Build and push helper images (writes/refreshes charts/lolday/helpers.lock)
bash scripts/build-helpers.sh

# 7. Deploy again — backend now starts clean
bash scripts/deploy.sh
```

## Teardown

```bash
bash scripts/teardown.sh
```

## Documentation

Start here for any new contributor / Claude Code session:

- [System architecture](docs/architecture.md) — components, data flows, env vars, tech debt, gotchas
- [Deploy runbook](docs/runbooks/deploy.md) — pre-requisites, K3s, GPU operator, Helm, verify, rollback
- [Troubleshooting](docs/runbooks/troubleshooting.md) — symptom → action lookup
- [Conventions](docs/conventions.md) — branch / commit / PR / phase / migration naming

Phase planning & history:

- [Phase specs](docs/superpowers/specs/) — per-phase design docs
- [Phase plans](docs/superpowers/plans/) — per-phase implementation plans
- [Phase history](docs/phase-history/) — past E2E checklists, retirement findings, debug write-ups
- [Postmortems](docs/postmortems/)

Originals (kept for traceability):

- [Original platform design spec](docs/superpowers/specs/2026-03-30-lolday-platform-design.md)
- [Phase 1 plan](docs/superpowers/plans/2026-03-30-phase1-infrastructure.md)
