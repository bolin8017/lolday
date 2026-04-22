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
#   Stage 5 — Any single PVC (generic)  (per-PVC downtime, post-Phase 9)
#
# Each stage is self-contained: run it, verify output, only move on
# once the verification passes. Rollback stanzas included for each.
#
# Run stages individually like:
#   sudo STAGE=1 bash scripts/migrate-ephemeral-to-ssd.sh
#   sudo STAGE=2 bash scripts/migrate-ephemeral-to-ssd.sh
#   sudo STAGE=5 NS=lolday PVC=lolday-grafana WORKLOAD=deploy/lolday-grafana \
#        bash scripts/migrate-ephemeral-to-ssd.sh
#   etc.

set -euo pipefail
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

# rsync_and_verify SRC DST — copy then GATE the bind-mount flip on
# byte-size + file-count agreement. This is the integrity check whose
# absence caused the Phase 8.2 live-fire data loss (see docs/2026-04-21-
# phase8-e2e-ux-findings.md post-mortem).
rsync_and_verify() {
  local SRC=$1 DST=$2
  echo ">>> rsync $SRC -> $DST"
  rsync -aHAX --one-file-system --info=progress2 --itemize-changes \
    --numeric-ids "$SRC/" "$DST/"

  local src_files dst_files src_bytes dst_bytes diff tol
  src_files=$(find "$SRC" -xdev -type f 2>/dev/null | wc -l)
  dst_files=$(find "$DST" -xdev -type f 2>/dev/null | wc -l)
  src_bytes=$(du -sxb "$SRC" 2>/dev/null | awk '{print $1}')
  dst_bytes=$(du -sxb "$DST" 2>/dev/null | awk '{print $1}')

  echo "  src: $src_files files / $src_bytes bytes"
  echo "  dst: $dst_files files / $dst_bytes bytes"

  if [ "$src_files" -ne "$dst_files" ]; then
    echo "FATAL: file count mismatch — NOT flipping bind mount" >&2
    return 1
  fi
  diff=$(( src_bytes > dst_bytes ? src_bytes - dst_bytes : dst_bytes - src_bytes ))
  # 0.1% of source size, floor 1 MiB — covers sparse/xattr accounting noise
  tol=$(( src_bytes / 1000 )); [ "$tol" -lt 1048576 ] && tol=1048576
  if [ "$diff" -gt "$tol" ]; then
    echo "FATAL: size divergence ${diff}B > tolerance ${tol}B — NOT flipping" >&2
    return 1
  fi
  echo "  OK."
}

# umount_recursive PATH — lazy-unmount every active mount below PATH.
# Stage 4 must call this before mv /var/lib/kubelet or the rename drags
# active bind-mounts into .old, confusing local-path-provisioner on
# restart (the 2026-04-21 data-loss root cause).
umount_recursive() {
  local ROOT=$1
  echo ">>> umount bind-mounts under $ROOT"
  mount | awk -v r="$ROOT" '$3 ~ "^"r { print $3 }' | tac | while read -r m; do
    umount -l "$m" 2>/dev/null || echo "  (skip) $m"
  done
}

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
  rsync_and_verify /var/lib/containerd "$SSD/containerd"

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
  rsync_and_verify /var/lib/docker "$SSD/docker"

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
  # Convenience wrapper for the biggest PV — Harbor registry. Stage 2 was
  # designed pre-Phase 9 with `statefulset` hardcoded, but Harbor 1.18.3 uses
  # a Deployment for the registry component. Delegates to Stage 5 so the
  # generic flow (rsync_and_verify + PV path patch + scale + smoke) stays
  # in one place.
  echo "=== STAGE 2 — Harbor registry PV (delegates to Stage 5) ==="
  NS=lolday \
  PVC=lolday-harbor-registry \
  WORKLOAD=deployment/lolday-harbor-registry \
  READY_SELECTOR="-l app=harbor,component=registry" \
  STAGE=5 exec bash "${BASH_SOURCE[0]}"
  ;;

# ─────────────────────────────────────────────────────────────────────────────
5)
  # Generic per-PVC migration from root-LV `/var/lib/rancher/k3s/storage/` to
  # `/mnt/ssd500g/k3s-storage/`. Works for any PVC fronted by a `local` PV on
  # this single-node cluster. Keeps the same PV object + same directory basename,
  # so claimRef/UID bindings survive and the only state that moves is bytes on
  # disk + one mutable field on the PV spec.
  #
  # Required env:
  #   NS=<namespace>                — where the PVC lives
  #   PVC=<pvc-name>                — the PVC to migrate
  #   WORKLOAD=<kind>/<name>        — what to scale down (deploy/X or statefulset/X)
  # Optional env:
  #   READY_SELECTOR="-l key=val"   — label-selector for `kubectl wait --for=ready`
  #                                   (defaults: infer from WORKLOAD)
  #   READY_TIMEOUT=180s            — per-pod readiness timeout
  #   SMOKE=<cmd>                   — shell command run after ready (non-zero => roll back manually)
  #
  # Fail-loud semantics:
  #   * `set -euo pipefail` at top of script → any unchecked failure aborts.
  #   * rsync_and_verify aborts BEFORE the PV patch if the integrity gate fails.
  #   * The old data directory is preserved at ${OLD_PATH}.old until the
  #     operator confirms the workload is healthy. Do NOT clean it up within
  #     this script — PV patches are reversible only while that data exists.
  : "${NS:?STAGE=5 requires NS=<namespace>}"
  : "${PVC:?STAGE=5 requires PVC=<pvc-name>}"
  : "${WORKLOAD:?STAGE=5 requires WORKLOAD=<kind>/<name>, e.g. deploy/mlflow}"
  READY_TIMEOUT="${READY_TIMEOUT:-180s}"

  echo "=== STAGE 5 — migrate PVC $NS/$PVC onto $SSD/k3s-storage ==="
  echo "  workload: $WORKLOAD"

  PV_NAME=$(kubectl -n "$NS" get pvc "$PVC" -o jsonpath='{.spec.volumeName}')
  [ -n "$PV_NAME" ] || { echo "FATAL: PVC $NS/$PVC has no bound PV" >&2; exit 1; }
  OLD_PATH=$(kubectl get pv "$PV_NAME" -o jsonpath='{.spec.local.path}')
  [ -n "$OLD_PATH" ] || { echo "FATAL: PV $PV_NAME has no spec.local.path (not a local-path PV?)" >&2; exit 1; }

  # Pin the new path to the same basename so we can reason about "this dir
  # equals this PV" post-migration too. Every local-path PV name is already
  # globally unique (pvc-<uuid>_<ns>_<claim>), so collisions are impossible.
  NEW_BASE=$(basename "$OLD_PATH")
  NEW_PATH="$SSD/k3s-storage/$NEW_BASE"

  echo "  PV   : $PV_NAME"
  echo "  OLD  : $OLD_PATH"
  echo "  NEW  : $NEW_PATH"

  # Idempotent states we can enter:
  #   A. Fresh:     OLD_PATH is a real dir, NEW_PATH absent  → full run
  #   B. Already-migrated: OLD_PATH is a bind-mount (over NEW_PATH) → abort-ok
  #   C. Half-done: OLD_PATH absent, ${OLD_PATH}.old exists, NEW_PATH exists
  #                → resume at step [4/8] (bind-mount setup)
  #   D. Unknown:   anything else → abort for operator inspection
  STATE=""
  if mountpoint -q "$OLD_PATH" 2>/dev/null; then
    STATE=B
  elif [ -d "$OLD_PATH" ] && [ ! -e "$NEW_PATH" ]; then
    STATE=A
  elif [ ! -e "$OLD_PATH" ] && [ -d "${OLD_PATH}.old" ] && [ -d "$NEW_PATH" ]; then
    STATE=C
  fi

  case "$OLD_PATH" in
    /mnt/ssd500g/*)
      echo "FATAL: $OLD_PATH already on SSD — Phase 9.6 assumes bind-mount"
      echo "       strategy (OLD_PATH stays under /var/lib/rancher/k3s/storage/,"
      echo "       SSD is the mount source). Your PV lives on SSD directly —"
      echo "       nothing to migrate." >&2
      exit 1 ;;
  esac

  case "$STATE" in
    B)
      echo "$OLD_PATH is already a bind-mount — already migrated, nothing to do." ; exit 0 ;;
    A)
      :  # normal path
      ;;
    C)
      echo "RESUME: detected half-done migration (data at $NEW_PATH, shelf at ${OLD_PATH}.old)"
      echo "        — skipping scale-down/rsync/shelve, resuming at mount step." ;;
    *)
      echo "FATAL: unexpected on-disk state — refusing to touch anything." >&2
      echo "  OLD_PATH ($OLD_PATH): $( [ -e "$OLD_PATH" ] && echo exists || echo absent )" >&2
      echo "  NEW_PATH ($NEW_PATH): $( [ -e "$NEW_PATH" ] && echo exists || echo absent )" >&2
      echo "  ${OLD_PATH}.old: $( [ -e "${OLD_PATH}.old" ] && echo exists || echo absent )" >&2
      echo "  mount: $( mountpoint -q "$OLD_PATH" 2>/dev/null && echo yes || echo no )" >&2
      exit 1 ;;
  esac

  # Default readiness selector if caller didn't provide one. StatefulSet pods
  # carry `statefulset.kubernetes.io/pod-name=<n>-0`; Deployments carry whatever
  # matchLabels they use, which we can't infer safely — require READY_SELECTOR
  # for Deployments or the caller must trust the scale-up timeout.
  if [ -z "${READY_SELECTOR:-}" ]; then
    case "$WORKLOAD" in
      statefulset/*|sts/*)
        STS_NAME=${WORKLOAD#*/}
        READY_SELECTOR="-l statefulset.kubernetes.io/pod-name=${STS_NAME}-0"
        ;;
      *)
        echo "  (no READY_SELECTOR; skipping wait — relying on scale timeout)"
        READY_SELECTOR=""
        ;;
    esac
  fi

  mkdir -p "$SSD/k3s-storage"

  if [ "$STATE" = "A" ]; then
    echo "[1/8] scaling $WORKLOAD → 0 replicas…"
    kubectl -n "$NS" scale "$WORKLOAD" --replicas=0
    # Wait for pods to disappear — `kubectl wait --for=delete` needs a live
    # resource to reference; use a polling fallback if nothing matches.
    if [ -n "$READY_SELECTOR" ]; then
      kubectl -n "$NS" wait --for=delete pod $READY_SELECTOR --timeout=120s 2>/dev/null || true
    fi
    sleep 5

    echo "[2/8] rsync $OLD_PATH → $NEW_PATH…"
    rsync_and_verify "$OLD_PATH" "$NEW_PATH"

    echo "[3/8] shelving old dir → ${OLD_PATH}.old …"
    mv "$OLD_PATH" "${OLD_PATH}.old"
  else
    echo "[1-3/8] skipped (resuming from half-done state)"
  fi

  # PV's .spec.local.path is immutable (apiserver rejects patches to
  # spec.persistentvolumesource). We cannot re-point the PV at the SSD path.
  # Instead: create an empty mount-point at OLD_PATH and bind-mount NEW_PATH
  # onto it — kubelet + pod see the canonical OLD_PATH (nothing in k8s changes)
  # while the actual blocks are served off /mnt/ssd500g. Same pattern as Stage
  # 1/1b for /var/lib/docker + /var/lib/containerd; /etc/fstab makes it
  # survive reboots.
  echo "[4/8] bind-mounting $NEW_PATH → $OLD_PATH (and fstab)…"
  mkdir -p "$OLD_PATH"
  # Re-apply directory metadata so k8s fsGroup/subPath assumptions keep
  # holding: copy uid/gid/mode from what we moved.
  if [ -d "${OLD_PATH}.old" ]; then
    SRC_STAT="${OLD_PATH}.old"
  else
    SRC_STAT="$NEW_PATH"
  fi
  chown --reference="$SRC_STAT" "$OLD_PATH"
  chmod --reference="$SRC_STAT" "$OLD_PATH"

  FSTAB_LINE="$NEW_PATH $OLD_PATH none bind 0 0"
  if ! grep -qxF "$FSTAB_LINE" /etc/fstab; then
    echo "$FSTAB_LINE" >> /etc/fstab
  fi
  systemctl daemon-reload 2>/dev/null || true
  mount "$OLD_PATH"
  mountpoint -q "$OLD_PATH" || { echo "FATAL: mount did not take effect" >&2; exit 1; }

  echo "[5/8] scaling $WORKLOAD → 1 replica…"
  kubectl -n "$NS" scale "$WORKLOAD" --replicas=1

  echo "[6/8] waiting for rollout (timeout $READY_TIMEOUT)…"
  # `kubectl rollout status` tracks the *current* generation's availability
  # against observedGeneration; `kubectl wait --for=ready pod <selector>`
  # matches every pod the label selector catches — including Succeeded/
  # Terminated leftovers from prior ReplicaSets, which are Ready=False
  # forever and block the wait. rollout status is the right primitive.
  if ! kubectl -n "$NS" rollout status "$WORKLOAD" --timeout="$READY_TIMEOUT"; then
    echo "FATAL: rollout did not become available after bind-mount migration." >&2
    echo "  Rollback (shelved data still at ${OLD_PATH}.old):" >&2
    echo "    kubectl -n $NS scale $WORKLOAD --replicas=0" >&2
    echo "    sudo umount $OLD_PATH" >&2
    echo "    sudo rmdir $OLD_PATH" >&2
    echo "    sudo sed -i '\\|^$NEW_PATH $OLD_PATH |d' /etc/fstab" >&2
    echo "    sudo mv ${OLD_PATH}.old $OLD_PATH" >&2
    echo "    kubectl -n $NS scale $WORKLOAD --replicas=1" >&2
    exit 1
  fi

  echo "[7/8] smoke test…"
  if [ -n "${SMOKE:-}" ]; then
    if ! eval "$SMOKE"; then
      echo "FATAL: smoke test failed — see rollback hint above." >&2
      exit 1
    fi
  else
    echo "  (no SMOKE command provided; pod Ready is the only signal)"
  fi

  echo "[8/8] disk state:"
  df -h / "$SSD" | awk 'NR==1 || /mapper|nvme0n1/'

  echo
  echo "=== Stage 5 complete for $NS/$PVC. Verify:"
  echo "  1. Workload behaves correctly end-to-end (exercise real traffic)."
  echo "  2. \`mountpoint $OLD_PATH\` prints 'is a mount point' + survives reboot via fstab."
  echo "  3. If OK after ~10 min: sudo rm -rf ${OLD_PATH}.old"
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
  cat <<'BANNER'
=== STAGE 4 — K3s containerd + kubelet → /mnt/ssd500g ===

┌─────────────────────────────────────────────────────────────────┐
│ DATA LOSS HISTORY                                               │
│                                                                 │
│ On 2026-04-21 this exact stage wiped the Harbor registry, MLflow│
│ artifacts, Grafana, Prometheus TSDB, Alertmanager, and Trivy    │
│ scan-DB local-path PVs. Root cause: kubelet bookkeeping under   │
│ /var/lib/kubelet.old confused local-path-provisioner into       │
│ re-provisioning the PV directories as empty.                    │
│                                                                 │
│ This revision adds:                                             │
│   • pre-stage scale-to-zero of stateful workloads (so they let  │
│     go of their volume mounts before kubelet is stopped)        │
│   • lazy umount of every bind-mount under /var/lib/kubelet      │
│     BEFORE the mv that would otherwise drag them into .old      │
│   • rsync_and_verify integrity gate (file-count + byte-size)    │
│     before each bind-mount flip                                 │
│                                                                 │
│ See docs/2026-04-21-phase8-e2e-ux-findings.md § "Stage 4        │
│ data-loss post-mortem" for the full detail.                     │
└─────────────────────────────────────────────────────────────────┘

Type exactly "I UNDERSTAND" at the prompt to continue, or ctrl+c.
BANNER
  read -r -p "> " CONFIRM
  if [ "$CONFIRM" != "I UNDERSTAND" ]; then
    echo "aborted (did not type 'I UNDERSTAND')." >&2
    exit 1
  fi

  mkdir -p "$SSD/k3s-containerd" "$SSD/kubelet"

  echo "[1/11] scaling stateful workloads to 0 (so kubelet releases volumes)…"
  kubectl -n lolday scale statefulset --all --replicas=0 2>/dev/null || true
  kubectl -n lolday scale deployment --all --replicas=0 2>/dev/null || true
  sleep 15

  echo "[2/11] stopping k3s…"
  systemctl stop k3s
  # Wait for child processes (kubelet, containerd-shims) to fully exit so
  # no file is held open while we rsync.
  sleep 5
  pkill -f '/var/lib/rancher/k3s/data/.*/bin/' 2>/dev/null || true
  sleep 3

  echo "[3/11] umount any bind-mounts still hanging under kubelet path…"
  # Critical: running mv on a tree with live bind-mounts leaves orphan mount
  # entries pointing to .old paths; local-path-provisioner then re-creates
  # the PV hostpath dirs empty on restart. This was the 2026-04-21 root
  # cause. Lazy umount everything under /var/lib/kubelet first.
  umount_recursive /var/lib/kubelet

  echo "[4/11] rsync K3s containerd (~12G) → $SSD/k3s-containerd…"
  rsync_and_verify /var/lib/rancher/k3s/agent/containerd "$SSD/k3s-containerd"

  echo "[5/11] rsync kubelet (~9G) → $SSD/kubelet…"
  rsync_and_verify /var/lib/kubelet "$SSD/kubelet"

  echo "[6/11] shelving originals → *.old …"
  mv /var/lib/rancher/k3s/agent/containerd /var/lib/rancher/k3s/agent/containerd.old
  mkdir /var/lib/rancher/k3s/agent/containerd
  mv /var/lib/kubelet /var/lib/kubelet.old
  mkdir /var/lib/kubelet

  echo "[7/11] adding fstab entries…"
  if ! grep -q "$SSD/k3s-containerd /var/lib/rancher/k3s/agent/containerd " /etc/fstab; then
    echo "$SSD/k3s-containerd /var/lib/rancher/k3s/agent/containerd none bind 0 0" >> /etc/fstab
  fi
  if ! grep -q "$SSD/kubelet /var/lib/kubelet " /etc/fstab; then
    echo "$SSD/kubelet /var/lib/kubelet none bind 0 0" >> /etc/fstab
  fi
  systemctl daemon-reload

  echo "[8/11] activating bind mounts…"
  mount /var/lib/rancher/k3s/agent/containerd
  mount /var/lib/kubelet
  mountpoint /var/lib/rancher/k3s/agent/containerd || { echo "MOUNT FAILED"; exit 1; }
  mountpoint /var/lib/kubelet || { echo "MOUNT FAILED"; exit 1; }

  echo "[9/11] starting k3s…"
  systemctl start k3s

  echo "[10/11] waiting for cluster to converge (up to 3min)…"
  for i in $(seq 1 36); do
    if kubectl get nodes >/dev/null 2>&1; then
      echo "  kubectl reachable after $((i*5))s"
      break
    fi
    sleep 5
  done
  # Scale statefulsets + deployments back up
  kubectl -n lolday scale statefulset --all --replicas=1 2>/dev/null || true
  kubectl -n lolday scale deployment --all --replicas=1 2>/dev/null || true

  echo "[11/11] pod + df state:"
  kubectl get nodes
  kubectl -n lolday get pods --no-headers 2>/dev/null | awk '{print "  "$1" "$3}' | head -20
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
  STAGE=2  — Harbor registry PV to SSD (delegates to Stage 5)
  STAGE=3  — local-path-provisioner default path (future PVCs only)
  STAGE=4  — K3s /var/lib/rancher to SSD (deepest; only if needed)
  STAGE=5  — Generic per-PVC migration to SSD. Required env:
               NS=<ns> PVC=<pvc-name> WORKLOAD=<kind>/<name>
             Optional env: READY_SELECTOR, READY_TIMEOUT, SMOKE

Recommended order: 1, verify ~5 min, 2, verify ~10 min,
then 3 (optional), then 4 (optional, only if / still pressured).

Rollback for each stage is in the script source near its main block.
EOF
  exit 2
  ;;
esac
