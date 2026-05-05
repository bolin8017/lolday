#!/usr/bin/env bash
# Patch K3s kubelet args on an existing server30 install.
#
# Adds kube-reserved + system-reserved + memory eviction so the Linux global
# OOM Killer can never reach kubelet. See:
#   docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §5.1
#
# Idempotent: re-runnable; always rewrites the drop-in to match the canonical
# values. SSH safety hard rule (CLAUDE.md): operator must verify SSH from a
# fresh session before --apply.
#
# Usage:
#   sudo bash scripts/patch-k3s-kubelet-args.sh             # dry-run (default)
#   sudo bash scripts/patch-k3s-kubelet-args.sh --apply     # actually patch + restart k3s
#   sudo bash scripts/patch-k3s-kubelet-args.sh --revert    # remove the drop-in
set -euo pipefail

DROPIN_DIR=/etc/systemd/system/k3s.service.d
DROPIN_FILE=${DROPIN_DIR}/10-lolday-kubelet-args.conf

# Canonical kubelet args — keep in sync with scripts/setup-k3s.sh
EXEC_OVERRIDE='[Service]
ExecStart=
ExecStart=/usr/local/bin/k3s server \
  --kubelet-arg=kube-reserved=cpu=1,memory=2Gi,ephemeral-storage=10Gi \
  --kubelet-arg=system-reserved=cpu=1,memory=4Gi,ephemeral-storage=10Gi \
  --kubelet-arg=eviction-hard=memory.available<1Gi,nodefs.available<10%,imagefs.available<10% \
  --kubelet-arg=eviction-soft=memory.available<2Gi,nodefs.available<15% \
  --kubelet-arg=eviction-soft-grace-period=memory.available=2m,nodefs.available=2m \
  --kubelet-arg=eviction-max-pod-grace-period=60'

mode=dry-run
case "${1:-}" in
  --apply) mode=apply ;;
  --revert) mode=revert ;;
  '' | --dry-run) mode=dry-run ;;
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

case "$mode" in
  dry-run)
    echo "[mode] dry-run (no changes)"
    echo "[plan] would write the following to ${DROPIN_FILE}:"
    echo "----"
    printf '%s\n' "${EXEC_OVERRIDE}"
    echo "----"
    echo "[plan] would then run: systemctl daemon-reload && systemctl restart k3s"
    echo "[plan] expected effect: ~30 seconds k3s server restart; pod runtime unaffected"
    echo ""
    echo "[next] re-run with --apply after verifying SSH from a fresh session"
    ;;
  apply)
    echo "[step 1/4] writing drop-in ${DROPIN_FILE}"
    mkdir -p "${DROPIN_DIR}"
    printf '%s\n' "${EXEC_OVERRIDE}" > "${DROPIN_FILE}"
    chmod 644 "${DROPIN_FILE}"

    echo "[step 2/4] daemon-reload"
    systemctl daemon-reload

    echo "[step 3/4] restart k3s"
    echo "[warn] expect ~30s control-plane downtime; SSH stays alive"
    systemctl restart k3s

    echo "[step 4/4] waiting for kubelet ready (60s timeout)..."
    ready=0
    for i in $(seq 1 30); do
      if kubectl get nodes 2>/dev/null | grep -q ' Ready '; then
        echo "[ok] kubelet ready after ${i}x2s"
        ready=1
        break
      fi
      sleep 2
    done

    if [ "${ready}" -ne 1 ]; then
      echo "[fatal] kubelet not ready after 60s" >&2
      echo "[hint] check 'journalctl -u k3s --since=2min' and 'systemctl cat k3s'" >&2
      exit 2
    fi

    echo "[done] applied. verify next:"
    echo "  kubectl get --raw /api/v1/nodes/server30/proxy/configz | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"kubeletconfig\"])'"
    ;;
  revert)
    echo "[step 1/3] removing drop-in"
    rm -f "${DROPIN_FILE}"
    rmdir --ignore-fail-on-non-empty "${DROPIN_DIR}" 2>/dev/null || true

    echo "[step 2/3] daemon-reload"
    systemctl daemon-reload

    echo "[step 3/3] restart k3s"
    systemctl restart k3s

    ready=0
    for i in $(seq 1 30); do
      if kubectl get nodes 2>/dev/null | grep -q ' Ready '; then
        echo "[ok] kubelet ready"
        ready=1
        break
      fi
      sleep 2
    done

    if [ "${ready}" -ne 1 ]; then
      echo "[fatal] kubelet not ready after 60s post-revert" >&2
      exit 2
    fi
    echo "[done] reverted"
    ;;
esac
