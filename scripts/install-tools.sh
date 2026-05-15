#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"

echo "=== Installing CLI tools to ${INSTALL_DIR} ==="
echo ""

# -------------------------------------------------------
# kubectl
# -------------------------------------------------------
echo "[1/5] kubectl..."
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
echo "[2/5] helm..."
if command -v helm &>/dev/null; then
  echo "  Already installed: $(helm version --short 2>/dev/null)"
else
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | \
    HELM_INSTALL_DIR="$INSTALL_DIR" USE_SUDO=false bash
fi

# -------------------------------------------------------
# k9s
# -------------------------------------------------------
echo "[3/5] k9s..."
if command -v k9s &>/dev/null; then
  echo "  Already installed: $(k9s version --short 2>/dev/null || echo 'yes')"
else
  K9S_VERSION="v0.50.18"
  curl -sL "https://github.com/derailed/k9s/releases/download/${K9S_VERSION}/k9s_Linux_amd64.tar.gz" | \
    tar xz -C "${INSTALL_DIR}" k9s
  echo "  Installed: ${K9S_VERSION}"
fi

# -------------------------------------------------------
# cosign (Sigstore — sign Harbor images per docs/runbooks/kyverno-harbor-signing.md)
# -------------------------------------------------------
echo "[4/5] cosign..."
if command -v cosign &>/dev/null; then
  echo "  Already installed: $(cosign version 2>&1 | grep -E '^GitVersion:' | awk '{print $2}' || echo yes)"
else
  COSIGN_TAG=$(curl -fsSL https://api.github.com/repos/sigstore/cosign/releases/latest \
    | grep -E '"tag_name":' | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
  TMP=$(mktemp -d)
  curl -fsSL "https://github.com/sigstore/cosign/releases/download/${COSIGN_TAG}/cosign-linux-amd64" \
    -o "${TMP}/cosign-linux-amd64"
  curl -fsSL "https://github.com/sigstore/cosign/releases/download/${COSIGN_TAG}/cosign_checksums.txt" \
    -o "${TMP}/cosign_checksums.txt"
  if ! grep "$(sha256sum "${TMP}/cosign-linux-amd64" | awk '{print $1}')" "${TMP}/cosign_checksums.txt" >/dev/null; then
    echo "  ERROR: cosign checksum mismatch — aborting" >&2
    rm -rf "${TMP}"
    exit 1
  fi
  mv "${TMP}/cosign-linux-amd64" "${INSTALL_DIR}/cosign"
  chmod +x "${INSTALL_DIR}/cosign"
  rm -rf "${TMP}"
  echo "  Installed: ${COSIGN_TAG}"
fi

# -------------------------------------------------------
# pre-commit (engineering hygiene)
# -------------------------------------------------------
echo "[5/5] pre-commit..."
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
  # pre-commit install "cowardly refuses" when core.hooksPath is set at all,
  # even if it points at the default location. Unset only when the value is
  # redundant (matches the default `.git/hooks` path); preserve genuine
  # custom hook directories.
  if HP=$(git -C "$REPO_ROOT" config --get core.hooksPath 2>/dev/null); then
    REPO_DEFAULT_HOOKS="${REPO_ROOT}/.git/hooks"
    if [ "$HP" = ".git/hooks" ] || [ "$HP" = "$REPO_DEFAULT_HOOKS" ]; then
      echo "  unsetting redundant core.hooksPath=${HP} (matches default)"
      git -C "$REPO_ROOT" config --unset-all core.hooksPath
    else
      echo "  WARN: core.hooksPath=${HP} is non-default; skipping 'pre-commit install'."
      echo "        Hooks still run via 'pre-commit run --all-files'."
      echo "        To activate the git-commit hook: 'git config --unset-all core.hooksPath' then re-run this script."
      SKIP_HOOK_INSTALL=1
    fi
  fi
  if [ "${SKIP_HOOK_INSTALL:-0}" != "1" ]; then
    (cd "$REPO_ROOT" && pre-commit install)
    echo "  Hook installed at ${REPO_ROOT}/.git/hooks/pre-commit"
  fi
else
  echo "  No .pre-commit-config.yaml at repo root; skipping hook activation"
fi

echo ""
echo "=== Done ==="
echo ""
echo "Make sure ${INSTALL_DIR} is in your PATH."
echo "Add to ~/.zshrc or ~/.bashrc if needed:"
echo "  export PATH=\"\${HOME}/.local/bin:\${PATH}\""
