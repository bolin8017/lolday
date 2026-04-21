#!/bin/bash
# Diagnose why K3s containerd gets 401 pulling backend:phase8 from Harbor.
# Read-only. Must run with sudo (needs /etc/rancher/k3s/registries.yaml).

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run as root (sudo)." >&2
  exit 1
fi

echo "=== 1. /etc/rancher/k3s/registries.yaml ==="
if [ -f /etc/rancher/k3s/registries.yaml ]; then
  cat /etc/rancher/k3s/registries.yaml
else
  echo "  (missing — K3s has no registry auth config)"
fi

echo
echo "=== 2. K3s containerd hosts.toml for harbor ==="
HOSTS_DIR=/var/lib/rancher/k3s/agent/etc/containerd/certs.d
ls -la "$HOSTS_DIR" 2>&1 | head -5
if [ -d "$HOSTS_DIR" ]; then
  find "$HOSTS_DIR" -name hosts.toml -print 2>/dev/null | while read -r f; do
    echo "--- $f ---"
    cat "$f"
    echo
  done
fi

echo
echo "=== 3. Direct crictl pull attempt ==="
CRICTL=$(ls /var/lib/rancher/k3s/data/current/bin/crictl 2>/dev/null | head -1)
if [ -z "$CRICTL" ]; then
  CRICTL=$(find /var/lib/rancher/k3s/data -name crictl 2>/dev/null | head -1)
fi
echo "crictl path: $CRICTL"
if [ -n "$CRICTL" ]; then
  "$CRICTL" --runtime-endpoint unix:///run/k3s/containerd/containerd.sock \
    pull harbor.lolday.svc:80/lolday/lolday-backend:phase8 2>&1 | tail -10
fi

echo
echo "=== 4. Harbor artifact list for lolday-backend ==="
# Load secrets as the user, then run curl as root (need access to secrets file)
if [ -f /home/bolin8017/.lolday-secrets.env ]; then
  HARBOR_ADMIN_PASSWORD=$(grep '^export HARBOR_ADMIN_PASSWORD=' /home/bolin8017/.lolday-secrets.env | cut -d= -f2-)
  kubectl run tmp-curl-$$ -n lolday --image=curlimages/curl --restart=Never --rm --command -- \
    sh -c "curl -s -u admin:$HARBOR_ADMIN_PASSWORD 'http://harbor.lolday.svc/api/v2.0/projects/lolday/repositories/lolday-backend/artifacts?with_tag=true&page_size=10' | head -c 800" 2>&1 | \
    grep -v 'pod/\|^[[:space:]]*$\|If you don' | head -15
else
  echo "  (secrets file missing)"
fi

echo
echo "=== 5. Current backend pod events (last pod) ==="
LAST_POD=$(kubectl -n lolday get pods -l app=backend --no-headers 2>/dev/null | tail -1 | awk '{print $1}')
if [ -n "$LAST_POD" ]; then
  echo "pod: $LAST_POD"
  kubectl -n lolday describe pod "$LAST_POD" 2>&1 | grep -E "Events:|Pulling|Failed|401|BackOff" | tail -10
fi

echo
echo "=== done ==="
