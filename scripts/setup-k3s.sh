#!/usr/bin/env bash
set -euo pipefail

# Must be run as root (sudo)
if [ "$(id -u)" -ne 0 ]; then
  echo "Error: This script must be run with sudo."
  echo "Usage: sudo bash scripts/setup-k3s.sh"
  exit 1
fi

# Detect the real user (not root) who invoked sudo
REAL_USER="${SUDO_USER:-}"
if [ -z "$REAL_USER" ]; then
  echo "Error: Cannot determine the real user. Run with: sudo bash scripts/setup-k3s.sh"
  exit 1
fi
REAL_HOME=$(eval echo "~${REAL_USER}")

echo "=== K3s Cluster Setup ==="
echo "User: ${REAL_USER}"
echo "Home: ${REAL_HOME}"
echo ""

# -------------------------------------------------------
# Pre-flight: Verify SSH will not be affected
# -------------------------------------------------------
echo "[0/3] Pre-flight checks..."
SSH_PORT=$(grep -E '^Port ' /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}')
SSH_PORT="${SSH_PORT:-22}"
echo "  SSH port: ${SSH_PORT}"
echo "  SSH service: $(systemctl is-active ssh)"
if ! systemctl is-active ssh &>/dev/null; then
  echo "  ERROR: SSH is not running. Aborting to prevent lockout."
  exit 1
fi
echo "  Pre-flight OK"
echo ""

# -------------------------------------------------------
# 1. Install K3s with default settings
# -------------------------------------------------------
echo "[1/3] Installing K3s (default Flannel + network policy + host safety reservations)..."
if systemctl is-active k3s &>/dev/null; then
  echo "  K3s already running. Skipping installation."
else
  # Phase 0 host safety net — see
  # docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §7
  INSTALL_K3S_EXEC="server \
    --kubelet-arg=kube-reserved=cpu=1,memory=2Gi,ephemeral-storage=10Gi \
    --kubelet-arg=system-reserved=cpu=1,memory=4Gi,ephemeral-storage=10Gi \
    --kubelet-arg=eviction-hard=memory.available<1Gi,nodefs.available<10%,imagefs.available<10% \
    --kubelet-arg=eviction-soft=memory.available<2Gi,nodefs.available<15% \
    --kubelet-arg=eviction-soft-grace-period=memory.available=2m,nodefs.available=2m \
    --kubelet-arg=eviction-max-pod-grace-period=60" \
  curl -sfL https://get.k3s.io | sh -
  echo "  Waiting for K3s to be ready..."
  until kubectl get nodes &>/dev/null; do
    sleep 2
  done
fi
echo "  K3s installed"
echo ""

# -------------------------------------------------------
# 2. Copy kubeconfig to user
# -------------------------------------------------------
echo "[2/3] Setting up kubeconfig for ${REAL_USER}..."
KUBE_DIR="${REAL_HOME}/.kube"
mkdir -p "$KUBE_DIR"
cp /etc/rancher/k3s/k3s.yaml "${KUBE_DIR}/config"
chown "${REAL_USER}:$(id -gn "$REAL_USER")" "${KUBE_DIR}/config"
chmod 600 "${KUBE_DIR}/config"
echo "  Kubeconfig written to ${KUBE_DIR}/config"
echo ""

# -------------------------------------------------------
# 3. Post-flight: Verify SSH still works
# -------------------------------------------------------
echo "[3/3] Post-flight checks..."
echo "  SSH service: $(systemctl is-active ssh)"
echo "  K3s service: $(systemctl is-active k3s)"
echo "  Node status:"
kubectl get nodes
echo ""

echo "=== K3s setup complete ==="
echo ""
echo "You can now run kubectl as ${REAL_USER} (no sudo needed)."
echo "Next step: install GPU Operator (see README.md)"
