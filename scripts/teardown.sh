#!/usr/bin/env bash
set -euo pipefail

echo "=== Lolday Teardown ==="
echo ""
echo "This will remove the lolday Helm release."
echo "PersistentVolumes with Retain policy will be kept."
echo ""
read -p "Continue? [y/N] " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

echo "[1/2] Removing lolday Helm release..."
helm uninstall lolday -n lolday 2>/dev/null || echo "  Not installed, skipping."

echo "[2/2] Removing GPU Operator..."
helm uninstall gpu-operator -n gpu-operator 2>/dev/null || echo "  Not installed, skipping."

echo ""
echo "=== Teardown complete ==="
echo ""
echo "K3s is still running. To fully remove K3s (requires sudo):"
echo "  sudo /usr/local/bin/k3s-uninstall.sh"
