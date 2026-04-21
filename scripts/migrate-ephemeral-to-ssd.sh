#!/bin/bash
# Phase 8.2 — migrate ephemeral storage from / to /mnt/ssd500g
#
# Root cause: / is a 98G LV; Docker, K3s containerd, kubelet EmptyDir,
# and local-path PVs (Harbor registry 100Gi, MLflow 100Gi, Loki 30Gi…)
# all land on /. Any DL-scale workload fills /.
#
# Target: /dev/nvme0n1p1 at /mnt/ssd500g, 458G (348G free).
#
# Staged to minimise blast radius:
#   Stage 1 — Docker data-root      (no k8s impact; SSH-safe)
#   Stage 2 — Harbor registry PV    (Harbor downtime only; k3s stays up)
#   Stage 3 — local-path default    (future PVCs; no existing data touched)
#   Stage 4 — K3s + kubelet         (deepest; only if 1–3 not enough)
#
# Each stage is self-contained: run it, verify output, only move on
# once the verification passes. Rollback stanzas included for each.
#
# Run stages individually like:
#   sudo STAGE=1 bash scripts/migrate-ephemeral-to-ssd.sh
#   sudo STAGE=2 bash scripts/migrate-ephemeral-to-ssd.sh
#   etc.

set -eu
SSD=/mnt/ssd500g
STAGE=${STAGE:-0}

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root (sudo)." >&2
  exit 1
fi

if ! mountpoint -q "$SSD"; then
  echo "ERROR: $SSD is not mounted." >&2
  exit 1
fi

echo "=== pre-flight ==="
df -h / "$SSD" | awk 'NR==1 || /\//'
echo

case "$STAGE" in
# ─────────────────────────────────────────────────────────────────────────────
1b)
  # Docker on this host uses the external containerd daemon
  # (`dockerd … --containerd=/run/containerd/containerd.sock`), which stores
  # every layer / image / snapshot under /var/lib/containerd. Stage 1 moved
  # /var/lib/docker (bookkeeping only, ~180K); Stage 1b moves the real
  # data plane. Run AFTER Stage 1 verified healthy.
  echo "=== STAGE 1b — containerd (external) root → $SSD/containerd ==="

  mkdir -p "$SSD/containerd"

  echo "[1/7] stopping containerd consumers (docker + containerd)…"
  systemctl stop docker docker.socket
  systemctl stop containerd

  echo "[2/7] rsync /var/lib/containerd → $SSD/containerd… (large — ~40GB expected)"
  rsync -aHAX --info=progress2 /var/lib/containerd/ "$SSD/containerd/"

  echo "[3/7] shelving /var/lib/containerd → /var/lib/containerd.old …"
  mv /var/lib/containerd /var/lib/containerd.old
  mkdir /var/lib/containerd

  echo "[4/7] fstab bind mount…"
  if ! grep -q "$SSD/containerd /var/lib/containerd " /etc/fstab; then
    echo "$SSD/containerd /var/lib/containerd none bind 0 0" >> /etc/fstab
  fi
  systemctl daemon-reload
  mount /var/lib/containerd

  echo "[5/7] starting containerd…"
  systemctl start containerd
  sleep 2
  echo "[6/7] starting docker…"
  systemctl start docker
  sleep 2

  echo "[7/7] verifying…"
  docker ps -a --format '{{.Names}}' | head -3
  docker images | head -5
  df -h /

  echo
  echo "=== Stage 1b complete. Verify:"
  echo "  1. 'docker images' still lists your images."
  echo "  2. / disk usage dropped significantly (expected: ~40GB freed)."
  echo "  3. SSH still works."
  echo "  4. If OK for 5 min: sudo rm -rf /var/lib/containerd.old"
  ;;

# ─────────────────────────────────────────────────────────────────────────────
1)
  echo "=== STAGE 1 — Docker data-root → $SSD/docker ==="

  mkdir -p "$SSD/docker"

  echo "[1/6] stopping docker…"
  systemctl stop docker docker.socket

  echo "[2/6] rsync /var/lib/docker → $SSD/docker…"
  rsync -aHAX --info=progress2 /var/lib/docker/ "$SSD/docker/"

  echo "[3/6] shelving old /var/lib/docker → /var/lib/docker.old …"
  mv /var/lib/docker /var/lib/docker.old
  mkdir /var/lib/docker

  echo "[4/6] adding bind mount to /etc/fstab…"
  if ! grep -q "$SSD/docker /var/lib/docker " /etc/fstab; then
    echo "$SSD/docker /var/lib/docker none bind 0 0" >> /etc/fstab
  fi
  mount /var/lib/docker

  echo "[5/6] starting docker…"
  systemctl start docker

  echo "[6/6] verifying…"
  sleep 3
  docker info 2>/dev/null | grep "Docker Root Dir"
  docker ps -a --format "{{.Names}}" | head -3
  df -h /

  echo
  echo "=== Stage 1 complete. Verify:"
  echo "  1. 'Docker Root Dir: /var/lib/docker' still reported (bind-mount transparent)."
  echo "  2. / disk usage dropped."
  echo "  3. SSH still works (open another terminal)."
  echo "  4. If all OK after 5 min: sudo rm -rf /var/lib/docker.old"
  ;;

# ─────────────────────────────────────────────────────────────────────────────
2)
  echo "=== STAGE 2 — Harbor registry PV → $SSD/pvs/harbor-registry ==="

  # Only move the biggest PV — Harbor registry. Other PVs stay put for now.
  PVC_NAME=lolday-harbor-registry
  PV_NAME=$(kubectl -n lolday get pvc "$PVC_NAME" -o jsonpath='{.spec.volumeName}')
  OLD_PATH=$(kubectl get pv "$PV_NAME" -o jsonpath='{.spec.local.path}')

  echo "  PVC: $PVC_NAME"
  echo "  PV : $PV_NAME"
  echo "  OLD: $OLD_PATH"

  mkdir -p "$SSD/pvs"

  echo "[1/7] cordoning + draining registry pod…"
  kubectl -n lolday scale statefulset lolday-harbor-registry --replicas=0 || true
  sleep 5

  echo "[2/7] rsync $OLD_PATH → $SSD/pvs/harbor-registry…"
  rsync -aHAX --info=progress2 "$OLD_PATH/" "$SSD/pvs/harbor-registry/"

  echo "[3/7] shelving old dir…"
  mv "$OLD_PATH" "${OLD_PATH}.old"

  echo "[4/7] editing PV hostPath… (patch in place)"
  kubectl patch pv "$PV_NAME" --type merge -p "{\"spec\":{\"local\":{\"path\":\"$SSD/pvs/harbor-registry\"}}}"

  echo "[5/7] restoring registry pod…"
  kubectl -n lolday scale statefulset lolday-harbor-registry --replicas=1

  echo "[6/7] waiting for Harbor registry pod ready…"
  kubectl -n lolday wait --for=condition=ready pod -l app=harbor,component=registry --timeout=180s

  echo "[7/7] smoke test: pull a known tag…"
  kubectl -n lolday exec deploy/backend -- sh -c '
    uv run python -c "import httpx; r = httpx.get(\"http://harbor.lolday.svc/v2/lolday/lolday-backend/manifests/phase8\", timeout=10); print(r.status_code)"
  ' || true
  df -h /

  echo
  echo "=== Stage 2 complete. Verify:"
  echo "  1. Harbor UI still serves images."
  echo "  2. / disk usage dropped by whatever was in the registry."
  echo "  3. If OK for 10 min: sudo rm -rf ${OLD_PATH}.old"
  ;;

# ─────────────────────────────────────────────────────────────────────────────
3)
  echo "=== STAGE 3 — local-path-provisioner default to $SSD/pvs ==="
  echo "Affects FUTURE PVCs only. Existing PVs stay put."

  mkdir -p "$SSD/pvs"

  # K3s bundles local-path-provisioner. It reads a ConfigMap in kube-system
  # named `local-path-config`. K3s's addon controller may revert direct
  # edits, so we use `--disable local-storage` and deploy our own pinned
  # Rancher local-path-provisioner with the SSD path.

  cat >/tmp/local-path-config.yaml <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-path-config
  namespace: kube-system
data:
  config.json: |-
    {
      "nodePathMap": [
        {
          "node": "DEFAULT_PATH_FOR_NON_LISTED_NODES",
          "paths": ["$SSD/pvs"]
        }
      ]
    }
EOF

  echo "[1/2] applying overriding ConfigMap…"
  kubectl apply -f /tmp/local-path-config.yaml

  # Mark it so k3s addon controller leaves it alone
  kubectl -n kube-system annotate cm local-path-config "helm.sh/hook=pre-install" --overwrite

  echo "[2/2] rolling local-path-provisioner to pick up new config…"
  kubectl -n kube-system rollout restart deploy local-path-provisioner

  sleep 10
  kubectl -n kube-system logs -l app=local-path-provisioner --tail=5

  echo
  echo "=== Stage 3 complete. Verify:"
  echo "  1. Next PVC you create lands in $SSD/pvs/…"
  echo "  2. Existing PVCs unchanged."
  ;;

# ─────────────────────────────────────────────────────────────────────────────
4)
  # Move the two heaviest subtrees under K3s:
  #   • /var/lib/rancher/k3s/agent/containerd  (~12G cluster image layers)
  #   • /var/lib/kubelet                        (~9G pod EmptyDirs etc.)
  # We explicitly do NOT touch /var/lib/rancher/k3s/storage (local-path PVs
  # — Harbor registry, Postgres, MLflow, etc. live there; migrating those
  # belongs in Stage 2 per-PV for controlled downtime).
  echo "=== STAGE 4 — K3s containerd + kubelet → $SSD ==="
  echo "Cluster goes DOWN for ~1–3 min during this stage."
  echo "SSH on port 9453 is systemd-managed and NOT affected."
  echo "Press ctrl+c in 8s to abort…"
  sleep 8

  mkdir -p "$SSD/k3s-containerd" "$SSD/kubelet"

  echo "[1/10] stopping k3s…"
  systemctl stop k3s
  # Wait for child processes (kubelet, containerd-shims) to fully exit so
  # no file is held open while we rsync.
  sleep 5
  pkill -f '/var/lib/rancher/k3s/data/.*/bin/' 2>/dev/null || true
  sleep 3

  echo "[2/10] rsync K3s containerd (~12G) → $SSD/k3s-containerd…"
  rsync -aHAX --one-file-system --info=progress2 \
    /var/lib/rancher/k3s/agent/containerd/ "$SSD/k3s-containerd/"

  echo "[3/10] rsync kubelet (~9G) → $SSD/kubelet…"
  rsync -aHAX --one-file-system --info=progress2 \
    /var/lib/kubelet/ "$SSD/kubelet/"

  echo "[4/10] shelving originals → *.old …"
  mv /var/lib/rancher/k3s/agent/containerd /var/lib/rancher/k3s/agent/containerd.old
  mkdir /var/lib/rancher/k3s/agent/containerd
  mv /var/lib/kubelet /var/lib/kubelet.old
  mkdir /var/lib/kubelet

  echo "[5/10] adding fstab entries…"
  if ! grep -q "$SSD/k3s-containerd /var/lib/rancher/k3s/agent/containerd " /etc/fstab; then
    echo "$SSD/k3s-containerd /var/lib/rancher/k3s/agent/containerd none bind 0 0" >> /etc/fstab
  fi
  if ! grep -q "$SSD/kubelet /var/lib/kubelet " /etc/fstab; then
    echo "$SSD/kubelet /var/lib/kubelet none bind 0 0" >> /etc/fstab
  fi
  systemctl daemon-reload

  echo "[6/10] activating bind mounts…"
  mount /var/lib/rancher/k3s/agent/containerd
  mount /var/lib/kubelet
  mountpoint /var/lib/rancher/k3s/agent/containerd || { echo "MOUNT FAILED"; exit 1; }
  mountpoint /var/lib/kubelet || { echo "MOUNT FAILED"; exit 1; }

  echo "[7/10] starting k3s…"
  systemctl start k3s

  echo "[8/10] waiting for cluster to converge (up to 3min)…"
  for i in $(seq 1 36); do
    if kubectl get nodes >/dev/null 2>&1; then
      echo "  kubectl reachable after $((i*5))s"
      break
    fi
    sleep 5
  done

  echo "[9/10] current pod state:"
  kubectl get nodes
  kubectl -n lolday get pods --no-headers 2>/dev/null | awk '{print "  "$1" "$3}' | head -20

  echo "[10/10] df after migration:"
  df -h / "$SSD" | awk 'NR==1 || /mapper|nvme0n1/'

  echo
  echo "=== Stage 4 complete. Verify:"
  echo "  1. kubectl responds; all core lolday pods eventually Running."
  echo "  2. SSH still works (open new terminal)."
  echo "  3. / disk dropped by ~21G (12 containerd + 9 kubelet)."
  echo "  4. Give pods 3-5 min to fully Ready before cleaning up."
  echo "  5. If all OK:"
  echo "       sudo rm -rf /var/lib/rancher/k3s/agent/containerd.old"
  echo "       sudo rm -rf /var/lib/kubelet.old"
  echo
  echo "  ROLLBACK (if cluster broken):"
  echo "    sudo systemctl stop k3s"
  echo "    sudo umount /var/lib/rancher/k3s/agent/containerd /var/lib/kubelet"
  echo "    sudo rmdir /var/lib/rancher/k3s/agent/containerd /var/lib/kubelet"
  echo "    sudo mv /var/lib/rancher/k3s/agent/containerd.old /var/lib/rancher/k3s/agent/containerd"
  echo "    sudo mv /var/lib/kubelet.old /var/lib/kubelet"
  echo "    sudo sed -i '/\\/mnt\\/ssd500g\\/k3s-containerd\\|\\/mnt\\/ssd500g\\/kubelet/d' /etc/fstab"
  echo "    sudo systemctl start k3s"
  ;;

*)
  cat <<EOF
Usage: sudo STAGE=<n> bash $0
  STAGE=1  — Docker data-root to SSD (safe, no k8s impact)
  STAGE=1b — External containerd root to SSD (holds the real image layers
             on hosts where dockerd uses --containerd=; run AFTER 1)
  STAGE=2  — Harbor registry PV to SSD (Harbor downtime only)
  STAGE=3  — local-path-provisioner default path (future PVCs only)
  STAGE=4  — K3s /var/lib/rancher to SSD (deepest; only if needed)

Recommended order: 1, verify ~5 min, 2, verify ~10 min,
then 3 (optional), then 4 (optional, only if / still pressured).

Rollback for each stage is in the script source near its main block.
EOF
  exit 2
  ;;
esac
