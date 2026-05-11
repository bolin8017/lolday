# Adding a new SSD to lolday storage

> ⚠️ **THIS RUNBOOK IS INVALIDATED — DO NOT USE AS-IS.**
>
> The loop-device validation on 2026-05-11 (Task 18) revealed an **architectural
> incompatibility** between this runbook's multi-pool approach and the official
> `minio/minio` Helm chart's standalone mode. See the "Validation history"
> section at the bottom for details. **A revised approach is required before
> the next real SSD is added.**
>
> Until the runbook is revised, **do not attempt SSD expansion via the steps
> below**. Instead, treat new SSDs as one of these alternatives (operator's
> choice based on context):
>
> 1. **OS-level disk replacement** (least disruption to application layer):
>    stop MinIO, RAID/LVM-merge old `/export` device with new SSD into a
>    single logical volume, remount over `/export`, restart MinIO. Application
>    layer unaware.
> 2. **MinIO Operator** (per-tenant pool support): switch off the
>    `minio/minio` chart, deploy `minio-operator` instead. Bigger change,
>    but properly supports horizontal pool expansion.
> 3. **Manual StatefulSet** (vendor a custom MinIO spec, drop the chart):
>    smallest delta from current state but loses chart upgrade ergonomics.
>
> See `docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md`
> §5.5, §8 for the original design and follow-up scope.

---

# Adding a new SSD to lolday storage (ORIGINAL DRAFT — see warning above)

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

### 2026-05-11 — Loop-device validation FAILED (invalidated the runbook above)

**Method**: `scripts/validate-add-ssd-runbook.sh` simulated a 10 GB SSD via
`losetup /dev/loop10` and walked Steps 1–3.

**Result**: ❌ MinIO `CrashLoopBackOff` immediately after the multi-pool helm
upgrade. Recovery required manual `kubectl patch deployment` to remove
the `/data2` volume + restoration via `helm rollback --no-hooks` + a final
`helm upgrade --reuse-values`.

**Root cause**: the `minio/minio` Helm chart 5.4.0 in `mode: standalone`
**hardcodes `/export` as the primary mount path** (not `/data1` as the
runbook's `extraArgs` assumed). When the helm upgrade injected
`extraVolumeMounts: /data2` + `extraArgs: server /data1 /data2`:

1. The Pod ended up with 3 mounts: `/tmp/credentials`, `/export`, `/data2`
2. MinIO args said "use /data1 /data2" — no /data1 mount → "drive not found"
3. MinIO then fell back to scanning available mounts, found `/export` (with
   existing format.json declaring "1 drive in 1 pool") and `/data2` (empty)
4. Erasure init refused: _"number of drives specified: 4 but the number of
   drives found in the 1st drive's format.json: 1"_ — drive count mismatch

The `extraArgs` injection pattern from `add-ssd.md` Step 3 above is
**fundamentally incompatible** with the chart's standalone mode. The
chart's pool config is bolted to `/export`; you cannot add a second pool
via `--set` without rewriting the chart template.

**Lessons captured**:

- The pattern documented above assumes a chart-level multi-pool flag that
  doesn't exist in `minio/minio` 5.4.0 standalone mode
- Validation with a loop-device caught this **before** a real SSD was needed
  — without this exercise, the operator would have discovered the bug at
  the worst possible moment (full disk + production data loss risk)
- Recovery was non-trivial: helm release got stuck in `failed` state due to
  init-Job pre-upgrade hook deadlock (Job waits for MinIO ready, MinIO can't
  be ready because of the bad config). Manual `kubectl patch` was the only
  unwedge path.

**Status of runbook**: INVALIDATED until rewritten with one of the
alternatives in the warning at the top of this file. Tracked as follow-up.
