# Lolday

Internal ML platform for ISLab malware detector management.

## Prerequisites

- K3s (installed by admin with `--flannel-backend=none --disable-network-policy`)
- kubectl, Helm, Cilium CLI, Trivy, Cloudflared, k9s
- NVIDIA drivers on GPU nodes

## Quick Start

```bash
# 1. Setup cluster-level components (once per cluster)
./scripts/setup-cluster.sh

# 2. Deploy the platform
./scripts/deploy.sh

# 3. Teardown (removes everything)
./scripts/teardown.sh
```

## Documentation

- [Design Spec](docs/superpowers/specs/2026-03-30-lolday-platform-design.md)
- [Phase 1: Infrastructure](docs/superpowers/plans/2026-03-30-phase1-infrastructure.md)
