#!/bin/bash
# Where is / actually being eaten? Run as root (needs to read /var/lib/*).
# Read-only — makes no changes.

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run as root (sudo)." >&2
  exit 1
fi

echo "=== df ==="
df -h / /mnt/ssd500g 2>/dev/null | awk 'NR==1 || /mapper|nvme0n1/'

echo
echo "=== docker images (confirm if still present) ==="
docker images -a | head -15

echo
echo "=== top disk consumers under /var/lib + /root ==="
du -sh /var/lib/*/ /root 2>/dev/null | sort -rh | head -15

echo
echo "=== shelved backups (from earlier migration stages) ==="
du -sh /var/lib/docker.old /var/lib/containerd.old 2>/dev/null

echo
echo "=== SSD usage after migration ==="
du -sh /mnt/ssd500g/*/ 2>/dev/null | sort -rh | head -10

echo
echo "=== /etc/containerd/config.toml (which root dir is configured?) ==="
cat /etc/containerd/config.toml 2>/dev/null | head -20

echo
echo "=== K3s embedded containerd (suspect biggest consumer) ==="
du -sh /var/lib/rancher/k3s/agent/containerd 2>/dev/null
du -sh /var/lib/rancher/k3s/agent/kubelet 2>/dev/null
du -sh /var/lib/rancher 2>/dev/null

echo
echo "=== crictl images in the cluster's containerd (K3s side) ==="
/var/lib/rancher/k3s/data/current/bin/crictl --runtime-endpoint unix:///run/k3s/containerd/containerd.sock images 2>/dev/null | head -10 || \
  k3s crictl images 2>/dev/null | head -10 || \
  echo "  crictl unavailable"
