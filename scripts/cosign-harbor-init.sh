#!/usr/bin/env bash
# Generate the cosign keypair that signs Harbor pushes for lolday, install
# the public half as a K8s Secret consumed by the Kyverno ClusterPolicy
# verify-lolday-harbor-image-signatures.
#
# Closes the operator-side bootstrap for issue #171 option 2 (sign Harbor
# pushes with a key, public key in cluster).
#
# Idempotent: re-running with an existing private key short-circuits the
# `cosign generate-key-pair` step. Re-running with an existing Secret
# performs an in-place `kubectl apply` (no key change).
#
# Usage:
#   bash scripts/cosign-harbor-init.sh                # interactive bootstrap
#   bash scripts/cosign-harbor-init.sh --force-new    # rotate: regenerate key,
#                                                     # replace the in-cluster Secret
#
# Runbook: docs/runbooks/kyverno-harbor-signing.md
# Spec rationale: issue #171, post-program review §3.12.
set -euo pipefail

COSIGN_DIR="${COSIGN_DIR:-$HOME/.cosign}"
PRIV_KEY="${COSIGN_DIR}/lolday-harbor.key"
PUB_KEY="${COSIGN_DIR}/lolday-harbor.pub"
SECRET_NS=kyverno
SECRET_NAME=cosign-harbor-pubkey
SECRET_FILE_KEY=cosign.pub   # Kyverno requires the key inside the Secret to be exactly `cosign.pub`.

mode=bootstrap
case "${1:-}" in
  --force-new) mode=rotate ;;
  '' | --bootstrap) mode=bootstrap ;;
  --help | -h)
    sed -n '2,/^set -euo pipefail$/p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *) echo "[fatal] unknown arg: $1" >&2; exit 1 ;;
esac

if ! command -v cosign >/dev/null 2>&1; then
  echo "[fatal] cosign not in PATH. Install via 'bash scripts/install-tools.sh'" >&2
  echo "        or download from https://github.com/sigstore/cosign/releases" >&2
  exit 1
fi
if ! command -v kubectl >/dev/null 2>&1; then
  echo "[fatal] kubectl not in PATH" >&2
  exit 1
fi

mkdir -p "${COSIGN_DIR}"
chmod 0700 "${COSIGN_DIR}"

# ---------------------------------------------------------------------------
# 1. Keypair — generate if missing, rotate on --force-new.
# ---------------------------------------------------------------------------
if [ "$mode" = "rotate" ] && [ -f "${PRIV_KEY}" ]; then
  STAMP=$(date +%s)
  echo "[step 1/3] rotating — moving existing keypair aside (suffix .pre-rotate-${STAMP})"
  mv "${PRIV_KEY}" "${PRIV_KEY}.pre-rotate-${STAMP}"
  mv "${PUB_KEY}"  "${PUB_KEY}.pre-rotate-${STAMP}" 2>/dev/null || true
fi

if [ -f "${PRIV_KEY}" ] && [ -f "${PUB_KEY}" ]; then
  echo "[step 1/3] keypair already exists at ${PRIV_KEY} — reusing"
else
  echo "[step 1/3] generating cosign keypair at ${PRIV_KEY}"
  echo
  echo "  cosign will prompt for a password to encrypt the private key."
  echo "  Pick a strong password and store it in your password manager —"
  echo "  the signing step in scripts/build-helpers.sh expects it via the"
  echo "  COSIGN_PASSWORD env var. Empty-password keys are rejected by"
  echo "  cosign newer than v2.2.0."
  echo
  # cosign writes cosign.key and cosign.pub to the working directory.
  ( cd "${COSIGN_DIR}" && cosign generate-key-pair )
  mv "${COSIGN_DIR}/cosign.key" "${PRIV_KEY}"
  mv "${COSIGN_DIR}/cosign.pub" "${PUB_KEY}"
  chmod 0600 "${PRIV_KEY}"
  chmod 0644 "${PUB_KEY}"
fi

# ---------------------------------------------------------------------------
# 2. Public key into the cluster as a Secret in the kyverno namespace.
# ---------------------------------------------------------------------------
if ! kubectl get ns "${SECRET_NS}" >/dev/null 2>&1; then
  echo "[fatal] namespace ${SECRET_NS} not found. Is Kyverno installed?" >&2
  echo "[hint]  run 'bash scripts/deploy.sh' first." >&2
  exit 1
fi

echo "[step 2/3] applying public key Secret ${SECRET_NS}/${SECRET_NAME}"
# `kubectl create --dry-run=client -o yaml | kubectl apply -f -` is the
# canonical idempotent Secret-create idiom (kubectl docs example). Strips
# the implicit `creationTimestamp` and `resourceVersion` from the manifest
# so apply sees a clean desired state.
kubectl create secret generic "${SECRET_NAME}" \
  --namespace="${SECRET_NS}" \
  --from-file="${SECRET_FILE_KEY}=${PUB_KEY}" \
  --dry-run=client -o yaml \
  | kubectl apply -f -

# ---------------------------------------------------------------------------
# 3. Print the public key + the rotation cadence.
# ---------------------------------------------------------------------------
echo
echo "[step 3/3] public key (safe to share; for offline verification):"
echo "----"
cat "${PUB_KEY}"
echo "----"
echo
echo "Key custody:"
echo "  Private:  ${PRIV_KEY} (chmod 600, operator-local)"
echo "  Public:   ${PUB_KEY}"
echo "  Public:   Secret ${SECRET_NS}/${SECRET_NAME} key ${SECRET_FILE_KEY}"
echo
echo "Rotation cadence:"
echo "  - Yearly hygiene rotation OR on compromise suspicion."
echo "  - Re-run with --force-new to rotate. Existing signatures REMAIN"
echo "    valid under the OLD key until the matching image tags are"
echo "    re-pushed under the NEW key; for a clean cutover, plan a"
echo "    re-push window after rotation."
echo
echo "Next steps:"
echo "  - Set COSIGN_PASSWORD in your shell (sourced from password manager)"
echo "    or pass it inline to scripts/build-helpers.sh."
echo "  - Test a signed push: scripts/build-helpers.sh"
echo "    (the post-push cosign sign step now runs automatically when"
echo "    ${PRIV_KEY} exists)."
echo "  - See docs/runbooks/kyverno-harbor-signing.md for the daily flow"
echo "    and the Audit->Enforce promotion procedure."
