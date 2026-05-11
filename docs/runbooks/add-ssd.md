# Adding a new SSD to lolday storage

> **Use this runbook when** server30's disk pressure crosses 80% (or anytime an
> operator decides to expand). It walks through plugging in a new SSD,
> formatting/mounting, and adding it as a new MinIO server pool — all
> **without touching MLflow / Harbor / Loki application config** and **without
> downtime**.
>
> Reference: `docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md` §5.5, §8.

## Prerequisites

- New SSD installed and visible in `lsblk` (e.g., `/dev/nvme1n1`)
- Temporary sudo on server30 (operator account granted; revoke after)
- MinIO running healthy: `kubectl get pod -n lolday -l app.kubernetes.io/name=minio` shows `Running`

## Step 1 — Format and mount (sudo)

```bash
# Identify new disk
sudo lsblk -d -o NAME,SIZE,MODEL | grep -v boot

# Format (XFS recommended by MinIO for ≥1 TB; ext4 fine otherwise).
sudo mkfs.xfs /dev/nvme1n1

# Mount at predictable path
sudo mkdir -p /mnt/ssd1
sudo mount /dev/nvme1n1 /mnt/ssd1
sudo chown 1001:1001 /mnt/ssd1 # MinIO container UID

# Persist across reboots
echo "/dev/nvme1n1 /mnt/ssd1 xfs defaults,nofail 0 0" | sudo tee -a /etc/fstab
```

## Step 2 — Create the PV + PVC pair pointing to the new mount

> **Why manual PV (not dynamic provision)**: local-path-provisioner's dynamic
> PV lands in `/var/lib/rancher/k3s/storage/` — it would NOT land on the new
> SSD. We bind a PV explicitly to `/mnt/ssd1`.

```bash
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: minio-data2
spec:
  capacity:
    storage: 1Ti # adjust to disk size
  accessModes: [ReadWriteOnce]
  persistentVolumeReclaimPolicy: Retain
  storageClassName: minio-local # NOT 'local-path' (avoid auto-provisioner)
  hostPath:
    path: /mnt/ssd1/minio
    type: DirectoryOrCreate
  claimRef:
    name: minio-data2-pvc
    namespace: lolday
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values: [server30]

---

apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: minio-data2-pvc
  namespace: lolday
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: minio-local
  resources:
    requests:
      storage: 1Ti
  volumeName: minio-data2
EOF
```

Verify PVC bound:

```bash
kubectl get pvc -n lolday minio-data2-pvc

# Expected output:
# NAME              STATUS   VOLUME        CAPACITY   ACCESS MODES   STORAGECLASS   AGE
# minio-data2-pvc   Bound    minio-data2   1Ti        RWO            minio-local     5s
```

## Step 3 — Extend MinIO StatefulSet with the new pool

Edit `charts/lolday/values.yaml`. In the `minio:` block, increase `drivesPerNode` and add the volume:

```yaml
minio:
  drivesPerNode: 2 # was 1
  extraVolumes:
    - name: data2
      persistentVolumeClaim:
        claimName: minio-data2-pvc
  extraVolumeMounts:
    - name: data2
      mountPath: /data2

  # Override args to use multi-pool (separate path args = separate pools).
  extraArgs:
    - server
    - /data1
    - /data2
    - --console-address
    - ":9001"
```

Apply via the standard deploy script:

```bash
bash scripts/deploy.sh
kubectl rollout status statefulset/minio -n lolday --timeout=5m
```

## Step 4 — Verify MinIO sees both pools

```bash
MINIO_POD=$(kubectl get pods -n lolday -l app.kubernetes.io/name=minio -o name | head -1)
kubectl exec -n lolday "$MINIO_POD" -- env MC_CONFIG_DIR=/tmp/mc mc admin info local

# Expected output (abridged):
# Drives: 2 OK
# Pools: 2
# Total capacity: <sum>
```

## Step 5 — Confirm new writes balance across pools

```bash
MINIO_POD=$(kubectl get pods -n lolday -l app.kubernetes.io/name=minio -o name | head -1)

# Submit a test job that produces new artifacts
# (Or wait for natural Loki / MLflow / Harbor traffic.)

# After some time, check usage on each drive
kubectl exec -n lolday "$MINIO_POD" -- df -h /data1 /data2
```

MinIO prefers writes to the less-full pool. **No application restart, no data migration, no config in MLflow / Harbor / Loki touched.**

## Step 6 — Add monitoring

Verify the new drive is in the existing storage Prometheus alert query
(`minio_cluster_capacity_usable_free_bytes` sums across pools automatically).

If `< 5 GB free` fires for the **whole MinIO instance** (not just one pool),
that means it's time to plan another SSD addition or upgrading to
multi-node. See `docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md` §5.6.

## Rollback

If Step 3 deploy fails:

```bash
helm rollback lolday # picks previous Helm revision
```

MinIO returns to single-pool mode. The new SSD remains formatted and mounted
(harmless idle). Delete the unused PV/PVC manually if desired:

```bash
kubectl delete pvc -n lolday minio-data2-pvc
kubectl delete pv minio-data2
```

## Validation history

(Empty — populated by Task 18 loopback validation and future operator runs.)
