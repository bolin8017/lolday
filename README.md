# Lolday

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

# 4. Deploy the platform (no sudo)
bash scripts/deploy.sh
```

## Teardown

```bash
bash scripts/teardown.sh
```

## Documentation

- [Design Spec](docs/superpowers/specs/2026-03-30-lolday-platform-design.md)
- [Phase 1 Plan](docs/superpowers/plans/2026-03-30-phase1-infrastructure.md)
