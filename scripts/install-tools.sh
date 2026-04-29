#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"

echo "=== Installing CLI tools to ${INSTALL_DIR} ==="
echo ""

# -------------------------------------------------------
# kubectl
# -------------------------------------------------------
echo "[1/4] kubectl..."
if command -v kubectl &>/dev/null; then
  echo "  Already installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client 2>&1 | head -1)"
else
  KUBECTL_VERSION=$(curl -L -s https://dl.k8s.io/release/stable.txt)
  curl -sLO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
  chmod +x kubectl
  mv kubectl "${INSTALL_DIR}/"
  echo "  Installed: ${KUBECTL_VERSION}"
fi

# -------------------------------------------------------
# helm
# -------------------------------------------------------
echo "[2/4] helm..."
if command -v helm &>/dev/null; then
  echo "  Already installed: $(helm version --short 2>/dev/null)"
else
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | \
    HELM_INSTALL_DIR="$INSTALL_DIR" USE_SUDO=false bash
fi

# -------------------------------------------------------
# k9s
# -------------------------------------------------------
echo "[3/4] k9s..."
if command -v k9s &>/dev/null; then
  echo "  Already installed: $(k9s version --short 2>/dev/null || echo 'yes')"
else
  K9S_VERSION="v0.50.18"
  curl -sL "https://github.com/derailed/k9s/releases/download/${K9S_VERSION}/k9s_Linux_amd64.tar.gz" | \
    tar xz -C "${INSTALL_DIR}" k9s
  echo "  Installed: ${K9S_VERSION}"
fi

# -------------------------------------------------------
# pre-commit (engineering hygiene)
# -------------------------------------------------------
echo "[4/4] pre-commit..."
if ! command -v uv &>/dev/null; then
  echo "  ERROR: uv is required to install pre-commit. Install uv first: https://docs.astral.sh/uv/" >&2
  exit 1
fi

if command -v pre-commit &>/dev/null; then
  echo "  Already installed: $(pre-commit --version)"
else
  uv tool install pre-commit
  echo "  Installed: $(pre-commit --version)"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "${REPO_ROOT}/.pre-commit-config.yaml" ]; then
  (cd "$REPO_ROOT" && pre-commit install)
  echo "  Hook installed at ${REPO_ROOT}/.git/hooks/pre-commit"
else
  echo "  No .pre-commit-config.yaml at repo root; skipping hook activation"
fi

echo ""
echo "=== Done ==="
echo ""
echo "Make sure ${INSTALL_DIR} is in your PATH."
echo "Add to ~/.zshrc or ~/.bashrc if needed:"
echo "  export PATH=\"\${HOME}/.local/bin:\${PATH}\""
