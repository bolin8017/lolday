#!/bin/bash
# Locate missing PV data. After Stage 4 the hostpath dirs at
# /var/lib/rancher/k3s/storage are empty (4K each). This script searches
# the whole host + SSD for directories that look like the lost registry
# or mlflow-artifacts data.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run with sudo" >&2; exit 1
fi

echo "=== find dirs named 'docker/registry' or 'v2/repositories' on / + SSD ==="
find / /mnt/ssd500g -type d \
  \( -name 'repositories' -o -name 'v2' -o -name 'artifacts' -o -name 'docker' \) \
  -not -path '/proc/*' -not -path '/sys/*' 2>/dev/null | head -30

echo
echo "=== find largest dirs under /var/lib that could be orphan PV data ==="
find /var/lib -maxdepth 5 -type d 2>/dev/null | xargs -I{} du -sh {} 2>/dev/null | sort -rh | head -15

echo
echo "=== find largest dirs under /mnt/ssd500g/kubelet ==="
find /mnt/ssd500g/kubelet -maxdepth 5 -type d 2>/dev/null | xargs -I{} du -sh {} 2>/dev/null | sort -rh | head -15

echo
echo "=== full /var/lib/rancher/k3s/storage breakdown ==="
du -sh /var/lib/rancher/k3s/storage/* 2>/dev/null | sort -rh

echo
echo "=== any large dirs elsewhere on host? ==="
du -sh /var/* /opt/* 2>/dev/null | awk '$1 ~ /[GM]/' | sort -rh | head -15

echo
echo "=== lsof of harbor-registry/mlflow pods — what are they mounting? ==="
for pod_label in "app=registry" "app=mlflow"; do
  POD=$(kubectl -n lolday get pods -l "$pod_label" --no-headers 2>/dev/null | awk 'NR==1{print $1}')
  [ -z "$POD" ] && continue
  echo "--- $POD ---"
  PID=$(kubectl -n lolday get pod "$POD" -o jsonpath='{.status.containerStatuses[0].containerID}' 2>/dev/null | sed 's|containerd://||')
  mount | grep "$PID" 2>/dev/null | head -3
done
