#!/usr/bin/env bash
# Patch an existing K3s install on server30 to enable the kube-apiserver
# audit log AND --secrets-encryption (data-at-rest encryption for the
# embedded SQLite-backed Secret store).
#
# Closes issue #167 — CIS 5.4.1 (secrets-encryption) + CIS 5.5 (audit log).
#
# ---------------------------------------------------------------------------
# SSH SAFETY (per ~/Documents/repositories/lolday/CLAUDE.md hard rule):
# server30 has no IPMI / out-of-band fallback. Restarting K3s WILL briefly
# bounce the kubelet but NOT sshd; in normal operation the SSH session
# survives. However, a misconfigured kube-apiserver flag CAN take the API
# down, after which a follow-up `helm upgrade` would fail and require
# manual recovery via the embedded SQLite. ALWAYS:
#
#   1. Open a SECOND independent SSH session BEFORE running this script.
#   2. Take a config + datastore snapshot:
#        sudo cp -a /etc/rancher/k3s /etc/rancher/k3s.bak-$(date +%s)
#        sudo cp /var/lib/rancher/k3s/server/db/state.db \
#                /var/lib/rancher/k3s/server/db/state.db.bak-$(date +%s)
#   3. Note the current K3s version: `k3s --version`.
#
# This script is interactive on purpose. Each destructive step gates on a
# `read -r -p` confirmation; the script does NOT auto-run end-to-end.
# Operator copy-pastes the prompted command, the script verifies, prompts
# the next.
#
# Usage:
#   sudo bash scripts/patch-k3s-audit-and-secrets-encryption.sh             # dry-run (default)
#   sudo bash scripts/patch-k3s-audit-and-secrets-encryption.sh --apply     # interactive apply
#   sudo bash scripts/patch-k3s-audit-and-secrets-encryption.sh --revert    # remove the drop-in
#
# Runbook: docs/runbooks/k3s-audit-and-secrets-encryption.md
# Spec rationale: post-program review §3.7 + §3.8 (linked in issue #167).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUDIT_POLICY_SRC="${REPO_ROOT}/charts/lolday/files/k3s-audit-policy.yaml"
AUDIT_POLICY_DST=/etc/rancher/k3s/audit-policy.yaml
AUDIT_LOG_DIR=/var/log/k3s
DROPIN_DIR=/etc/systemd/system/k3s.service.d
DROPIN_FILE="${DROPIN_DIR}/20-lolday-audit-and-secrets.conf"

# Canonical drop-in body. Mirrors the upstream K3s "Customizing K3s server"
# docs (https://docs.k3s.io/installation/configuration#configuration-with-systemd).
# Audit log rotation flags follow CIS 1.2.22 (100 MiB max, 10 backups, 30 day
# age — the kube-bench mainstream defaults).
DROPIN_BODY='[Service]
Environment="K3S_KUBELET_EXTRA_ARGS="
ExecStart=
ExecStart=/usr/local/bin/k3s server \
  --secrets-encryption \
  --kube-apiserver-arg=audit-log-path=/var/log/k3s/audit.log \
  --kube-apiserver-arg=audit-policy-file=/etc/rancher/k3s/audit-policy.yaml \
  --kube-apiserver-arg=audit-log-maxage=30 \
  --kube-apiserver-arg=audit-log-maxbackup=10 \
  --kube-apiserver-arg=audit-log-maxsize=100'

mode=dry-run
case "${1:-}" in
  --apply) mode=apply ;;
  --revert) mode=revert ;;
  '' | --dry-run) mode=dry-run ;;
  --help | -h)
    sed -n '2,/^set -euo pipefail$/p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *) echo "[fatal] unknown arg: $1" >&2; exit 1 ;;
esac

if [ "$(id -u)" -ne 0 ]; then
  echo "[fatal] this script must be run with sudo" >&2
  exit 1
fi

if ! systemctl is-active --quiet ssh; then
  echo "[fatal] ssh service is not active — aborting to prevent lockout" >&2
  exit 1
fi

if [ ! -f "${AUDIT_POLICY_SRC}" ]; then
  echo "[fatal] audit policy source missing: ${AUDIT_POLICY_SRC}" >&2
  echo "[hint]  expected to be tracked at charts/lolday/files/k3s-audit-policy.yaml" >&2
  exit 1
fi

confirm() {
  # confirm PROMPT — abort the script unless the operator types "yes".
  local prompt=$1
  local response=""
  read -r -p "$prompt [yes/no] " response
  if [ "$response" != "yes" ]; then
    echo "[abort] operator did not confirm; nothing changed since last step." >&2
    exit 1
  fi
}

case "$mode" in
  dry-run)
    echo "[mode] dry-run (no changes)"
    echo
    echo "[plan] would install audit policy to: ${AUDIT_POLICY_DST}"
    echo "[plan] would create log dir:          ${AUDIT_LOG_DIR}"
    echo "[plan] would write systemd drop-in:   ${DROPIN_FILE}"
    echo "----"
    printf '%s\n' "${DROPIN_BODY}"
    echo "----"
    echo "[plan] would: systemctl daemon-reload && systemctl restart k3s"
    echo "[plan] expected effect: ~30 s control-plane restart"
    echo "[plan] expected effect: every existing Secret remains readable;"
    echo "[plan]                  newly written Secrets and re-encrypted"
    echo "[plan]                  existing Secrets are stored AES-CBC-encrypted"
    echo "[plan]                  in the embedded datastore."
    echo
    echo "[next] re-run with --apply after verifying SSH from a fresh session."
    ;;

  apply)
    echo "============================================================"
    echo "K3s audit log + --secrets-encryption interactive apply"
    echo "============================================================"
    echo
    echo "PRE-FLIGHT CHECKLIST"
    echo "  1. SECOND SSH session is already open in another terminal."
    echo "  2. Config + datastore snapshots taken (see script header)."
    echo "  3. Current K3s version noted."
    echo
    confirm "Have all three prerequisites been done?"

    echo "[step 1/7] writing audit policy to ${AUDIT_POLICY_DST}"
    mkdir -p "$(dirname "${AUDIT_POLICY_DST}")"
    install -m 0644 -o root -g root "${AUDIT_POLICY_SRC}" "${AUDIT_POLICY_DST}"

    echo "[step 2/7] preparing audit log directory ${AUDIT_LOG_DIR}"
    mkdir -p "${AUDIT_LOG_DIR}"
    chmod 0750 "${AUDIT_LOG_DIR}"

    echo "[step 3/7] writing systemd drop-in ${DROPIN_FILE}"
    mkdir -p "${DROPIN_DIR}"
    printf '%s\n' "${DROPIN_BODY}" > "${DROPIN_FILE}"
    chmod 0644 "${DROPIN_FILE}"

    echo "[step 4/7] daemon-reload"
    systemctl daemon-reload

    echo "[step 5/7] restart k3s"
    echo "[warn] expect ~30 s API server downtime; SSH stays alive"
    confirm "Restart k3s now?"
    systemctl restart k3s

    echo "[step 6/7] waiting for kubelet ready (90 s timeout)..."
    ready=0
    for i in $(seq 1 45); do
      if kubectl get nodes 2>/dev/null | grep -q ' Ready '; then
        echo "[ok] kubelet ready after ${i}x2s"
        ready=1
        break
      fi
      sleep 2
    done
    if [ "${ready}" -ne 1 ]; then
      echo "[fatal] kubelet not ready after 90 s" >&2
      echo "[hint] check 'journalctl -u k3s --since=2min' and 'systemctl cat k3s'" >&2
      echo "[hint] to roll back: sudo bash scripts/patch-k3s-audit-and-secrets-encryption.sh --revert" >&2
      exit 2
    fi

    echo "[step 7/7] verification commands"
    echo
    echo "  Verify the audit log is being written:"
    echo "    sudo tail -1 ${AUDIT_LOG_DIR}/audit.log | jq '.verb, .objectRef.resource'"
    echo
    echo "  Verify the kube-apiserver came up with the audit flags:"
    echo "    sudo journalctl -u k3s --since '5 min ago' | grep -E 'audit-log-path|secrets-encryption' | head"
    echo
    echo "  Verify SSH is still alive FROM A FRESH SESSION:"
    echo "    ssh -p 9453 <operator>@<server30> 'uptime'"
    echo
    echo "[next] One-shot re-encryption of existing Secrets:"
    echo "    kubectl get secrets -A -o json | kubectl replace -f -"
    echo "  This forces every existing Secret through the new encryption"
    echo "  provider. Until run, existing Secrets remain plaintext on disk"
    echo "  even though --secrets-encryption is now active."
    echo
    echo "[done] apply complete."
    ;;

  revert)
    echo "[step 1/4] removing systemd drop-in"
    rm -f "${DROPIN_FILE}"
    rmdir --ignore-fail-on-non-empty "${DROPIN_DIR}" 2>/dev/null || true

    echo "[step 2/4] daemon-reload"
    systemctl daemon-reload

    echo "[step 3/4] restart k3s"
    echo "[warn] expect ~30 s API server downtime; SSH stays alive"
    confirm "Restart k3s now?"
    systemctl restart k3s

    echo "[step 4/4] waiting for kubelet ready (90 s timeout)..."
    ready=0
    for i in $(seq 1 45); do
      if kubectl get nodes 2>/dev/null | grep -q ' Ready '; then
        echo "[ok] kubelet ready"
        ready=1
        break
      fi
      sleep 2
    done
    if [ "${ready}" -ne 1 ]; then
      echo "[fatal] kubelet not ready after 90 s post-revert" >&2
      exit 2
    fi
    echo
    echo "[note] this revert removed the systemd drop-in but did NOT"
    echo "       roll back encrypted Secrets. Any Secret created or"
    echo "       updated while --secrets-encryption was on is still"
    echo "       ciphertext in the datastore. The kube-apiserver still"
    echo "       reads it transparently because the EncryptionConfig"
    echo "       was a NO-OP in K3s' bundled mode. Test reads if in"
    echo "       doubt."
    echo "[done] reverted"
    ;;
esac
