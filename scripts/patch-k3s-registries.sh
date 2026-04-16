#!/usr/bin/env bash
# Idempotently add Harbor mirror to K3s containerd registries.yaml.
# Must be run with sudo.
#
# Safety (SSH on 9453 must not break):
#   1. Backup current file → .bak.<timestamp>
#   2. Read Harbor Service ClusterIP dynamically
#   3. Dry-run diff; prompt user to confirm
#   4. Write new file, restart k3s
#   5. Verify k3s is-active; rollback on failure
set -euo pipefail

FILE=/etc/rancher/k3s/registries.yaml
NAMESPACE=lolday
SERVICE=harbor

if [[ $EUID -ne 0 ]]; then
  echo "This script requires sudo." >&2
  exit 1
fi

CALLER="${SUDO_USER:-root}"

# 1. Read Harbor ClusterIP (use caller's kubeconfig; root typically has no kubeconfig on K3s)
if [[ "$CALLER" != "root" ]]; then
  CALLER_HOME=$(eval echo "~$CALLER")
  CLUSTER_IP=$(sudo -u "$CALLER" KUBECONFIG="$CALLER_HOME/.kube/config" kubectl get svc -n "$NAMESPACE" "$SERVICE" -o jsonpath='{.spec.clusterIP}')
else
  # Root: fall back to K3s's built-in kubeconfig
  CLUSTER_IP=$(KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl get svc -n "$NAMESPACE" "$SERVICE" -o jsonpath='{.spec.clusterIP}')
fi
if [[ -z "$CLUSTER_IP" ]]; then
  echo "Failed to read Harbor ClusterIP (svc $SERVICE in ns $NAMESPACE). Is Harbor deployed?" >&2
  exit 1
fi
echo "Detected Harbor ClusterIP: $CLUSTER_IP"

TS=$(date +%Y%m%d-%H%M%S)
BACKUP="${FILE}.bak.${TS}"

# 2. Backup (create file if missing)
if [[ -f "$FILE" ]]; then
  cp -v "$FILE" "$BACKUP"
else
  install -m 0600 -o root -g root /dev/null "$FILE"
  echo "# Managed by lolday/patch-k3s-registries.sh" > "$FILE"
  echo "mirrors: {}" >> "$FILE"
fi

# 3. Compose new content
NEW=$(mktemp)
python3 - "$FILE" "$CLUSTER_IP" <<'PY' > "$NEW"
import sys, yaml
path, cluster_ip = sys.argv[1], sys.argv[2]
with open(path) as f:
    data = yaml.safe_load(f) or {}
mirrors = data.setdefault("mirrors", {})
mirrors["harbor.lolday.svc:80"] = {
    "endpoint": [f"http://{cluster_ip}:80"],
}
print(yaml.safe_dump(data, sort_keys=True))
PY

# 4. Show diff, prompt
echo "--- proposed changes ---"
diff -u "$FILE" "$NEW" || true
echo "------------------------"
read -r -p "Apply? [y/N] " ans
if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
  echo "aborted"
  rm -f "$NEW"
  exit 0
fi

mv "$NEW" "$FILE"
chmod 0600 "$FILE"

# 5. Restart k3s + verify
echo "Restarting k3s..."
systemctl restart k3s
sleep 5
if ! systemctl is-active --quiet k3s; then
  echo "CRITICAL: k3s failed to start; rolling back from $BACKUP" >&2
  cp "$BACKUP" "$FILE"
  systemctl restart k3s
  sleep 3
  if systemctl is-active --quiet k3s; then
    echo "rollback succeeded; exiting with failure" >&2
    exit 2
  else
    echo "CRITICAL: k3s still not active after rollback; investigate immediately (SSH at port 9453 should still work)" >&2
    exit 3
  fi
fi
echo "k3s restarted successfully. Backup kept at: $BACKUP"
