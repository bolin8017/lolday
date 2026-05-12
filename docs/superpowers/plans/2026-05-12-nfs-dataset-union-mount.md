# NFS Dataset Union Mount Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose three NFS-backed sample banks (`benignware/data/`, `nict202403/nictMalware/`, `nict202503/nictMalware/`) on server14 as a single lolday `samples_root` via mergerfs FUSE union, with zero copy and zero backend code change.

**Architecture:** Host-side mergerfs read-only union at `/mnt/lolday-samples`; chart `samples.hostPath` flips to point at the union; PV/PVC re-create on `helm upgrade` (spec.hostPath is not in the K8s mutable PV field set). Backend `_sample_path(root, sha256)` contract is unchanged.

**Tech Stack:** mergerfs (FUSE), systemd fstab, Helm, K3s, kubectl, lolday backend (FastAPI).

**Spec:** `docs/superpowers/specs/2026-05-12-nfs-dataset-union-mount-design.md`

---

## Pre-flight checklist (read once before starting)

Confirm all are true before Phase 1:

- [ ] You are on a fresh SSH session into server30 (port 9453); you have a backup terminal open
- [ ] No detector training jobs are currently running (`kubectl -n lolday-jobs get vcjob` returns empty or only completed/failed)
- [ ] NFS mount is up: `mountpoint /mnt/server14/dataset` returns success
- [ ] You are on branch `docs/nfs-dataset-union-mount-spec` (the spec commit is local but not yet pushed) — work continues on this branch
- [ ] You have ~30 minutes of uninterrupted time (this is a single-pass change; partial state is recoverable but inconvenient)

**Maintenance window note:** Phase 2 takes the backend Deployment down for ~2-5 minutes during PV/PVC recreate. Coordinate with lab if anyone is actively using the lolday UI.

---

## Phase 1: Host union mount

### Task 1.1: Install mergerfs and FUSE

**Files:**

- Modify (system): apt package state

- [ ] **Step 1: Confirm mergerfs is not already installed**

Run:

```bash
dpkg -l mergerfs fuse 2>&1 | tail -5
which mergerfs
```

Expected: `mergerfs` package shows `un` or `dpkg-query: no packages found` for mergerfs; `which mergerfs` returns nothing.

- [ ] **Step 2: Install fuse and mergerfs**

Run:

```bash
sudo apt-get update
sudo apt-get install -y fuse mergerfs
```

Expected: install completes; no errors.

- [ ] **Step 3: Verify install**

Run:

```bash
mergerfs --version
which mergerfs
```

Expected: prints version (≥ 2.35); `which` returns `/usr/bin/mergerfs`.

- [ ] **Step 4: Verify fuse kernel module loaded**

Run:

```bash
lsmod | grep fuse
```

Expected: a `fuse` line is present.

---

### Task 1.2: Create mount point directory

**Files:**

- Create (system): `/mnt/lolday-samples` (directory)

- [ ] **Step 1: Confirm directory does not yet exist**

Run:

```bash
ls -ld /mnt/lolday-samples 2>&1
```

Expected: `ls: cannot access '/mnt/lolday-samples': No such file or directory`. If it exists and is non-empty, stop and investigate before proceeding.

- [ ] **Step 2: Create empty mount point**

Run:

```bash
sudo mkdir -p /mnt/lolday-samples
sudo chmod 755 /mnt/lolday-samples
sudo chown root:root /mnt/lolday-samples
```

Expected: no output (success).

- [ ] **Step 3: Verify empty + correct perms**

Run:

```bash
ls -ld /mnt/lolday-samples
ls -la /mnt/lolday-samples/
```

Expected: `drwxr-xr-x 2 root root ...`, and the directory listing shows only `.` and `..`.

---

### Task 1.3: Backup fstab and add mergerfs entry

**Files:**

- Modify (system): `/etc/fstab` (append one line)

- [ ] **Step 1: Backup current fstab**

Run:

```bash
sudo cp /etc/fstab /etc/fstab.bak-$(date -u +%Y%m%dT%H%M%SZ)
sudo ls -la /etc/fstab.bak-*
```

Expected: backup file listed with current timestamp.

- [ ] **Step 2: Verify the NFS source paths all exist and are populated**

Run:

```bash
for p in \
  /mnt/server14/dataset/nict202503/nictMalware \
  /mnt/server14/dataset/nict202403/nictMalware \
  /mnt/server14/dataset/benignware/data; do
  echo "--- $p ---"
  ls "$p" 2>&1 | head -5
done
```

Expected: each path lists `00 01 02 ...` hex sub-directories. If any is empty or "No such file", stop and verify NFS state before proceeding.

- [ ] **Step 3: Append the mergerfs union line to fstab**

Run (one command, do not split — the colon-separated paths must stay together):

```bash
echo '/mnt/server14/dataset/nict202503/nictMalware:/mnt/server14/dataset/nict202403/nictMalware:/mnt/server14/dataset/benignware/data  /mnt/lolday-samples  fuse.mergerfs  allow_other,use_ino,category.action=ro,category.search=ff,minfreespace=0,nofail,_netdev  0  0' | sudo tee -a /etc/fstab
```

Expected: stdout echoes the line back.

- [ ] **Step 4: Verify fstab parse**

Run:

```bash
sudo findmnt --verify /etc/fstab
```

Expected: no errors. If any warning mentions the mergerfs line, fix the line before continuing — typos here can cause boot hangs (mitigated by `nofail`, but still avoid).

---

### Task 1.4: Activate the mount

**Files:**

- Modify (system): runtime mount state at `/mnt/lolday-samples`

- [ ] **Step 1: Run mount from fstab**

Run:

```bash
sudo mount -a
```

Expected: no output (success). Errors here usually mean a typo in the fstab line.

- [ ] **Step 2: Verify mount is active**

Run:

```bash
mountpoint /mnt/lolday-samples && echo MOUNTED
mount | grep lolday-samples
```

Expected:

```
/mnt/lolday-samples is a mountpoint
MOUNTED
<source-list> on /mnt/lolday-samples type fuse.mergerfs (ro,nosuid,nodev,relatime,user_id=0,group_id=0,allow_other)
```

(actual `ro` is enforced by `category.action=ro` even if the mount line itself says `rw`)

- [ ] **Step 3: Verify union view shows 256 hex sub-directories**

Run:

```bash
ls /mnt/lolday-samples/ | sort | head -5
ls /mnt/lolday-samples/ | wc -l
```

Expected: `00 01 02 03 04` (sorted); count is 256 (since all three branches contribute the full hex prefix range).

- [ ] **Step 4: Verify dedup priority (2025 first) on a known cross-bank SHA-256**

Run (pick a SHA-256 that exists in both 2024 and 2025 banks — try one from `/mnt/server14/dataset/nict202503/nictMalware/00/` and check if same name exists at `/mnt/server14/dataset/nict202403/nictMalware/00/`):

```bash
# Find a candidate cross-bank file
ls /mnt/server14/dataset/nict202503/nictMalware/00/ | head -1 > /tmp/candidate_sha
CANDIDATE=$(cat /tmp/candidate_sha)
echo "Candidate: $CANDIDATE"
echo "Exists in 2025: $(ls /mnt/server14/dataset/nict202503/nictMalware/00/$CANDIDATE 2>&1)"
echo "Exists in 2024: $(ls /mnt/server14/dataset/nict202403/nictMalware/00/$CANDIDATE 2>&1)"
echo "Exists in benignware: $(ls /mnt/server14/dataset/benignware/data/00/$CANDIDATE 2>&1)"
echo ""
echo "Via union view:"
stat /mnt/lolday-samples/00/$CANDIDATE
```

Expected: `stat` succeeds. If candidate exists in multiple branches, the union resolves to the **first one in fstab order** (nict202503 wins). This can be confirmed by comparing `Inode:` against the source files (same inode as the 2025 file).

- [ ] **Step 5: Verify sample-from-each-bank resolution**

Run:

```bash
# 2025 (first branch)
ls /mnt/server14/dataset/nict202503/nictMalware/01/ | head -1 \
  | xargs -I{} stat /mnt/lolday-samples/01/{} | head -3
# 2024 (second branch)
ls /mnt/server14/dataset/nict202403/nictMalware/02/ | head -1 \
  | xargs -I{} stat /mnt/lolday-samples/02/{} | head -3
# benignware (third branch)
ls /mnt/server14/dataset/benignware/data/03/ | head -1 \
  | xargs -I{} stat /mnt/lolday-samples/03/{} | head -3
```

Expected: all three `stat` calls succeed. The first line of each output is `File: /mnt/lolday-samples/<prefix>/<sha256>`.

If any of these fails: STOP. Do not proceed to Phase 2. Check `mount`, `dmesg`, `journalctl -u mergerfs*`.

---

## Phase 2: K8s chart cutover

> ⚠️ **Heads up:** this phase takes the backend Deployment down for 2-5 minutes during PV/PVC recreate. Do not run during active platform use.

### Task 2.1: Update chart values

**Files:**

- Modify: `charts/lolday/values.yaml` (line 260)

- [ ] **Step 1: Confirm current value**

Run:

```bash
grep -n -A1 '^samples:' charts/lolday/values.yaml
```

Expected output:

```
256:# Sample Datasets (hostPath PVs on server30)
257:
258:samples:
259:  enabled: ...
260:  hostPath: /mnt/ssd500g/data/samples
```

- [ ] **Step 2: Edit values.yaml to point at the union mount**

Use the Edit tool (or open in editor) to change line 260:

```yaml
# from
  hostPath: /mnt/ssd500g/data/samples
# to
  hostPath: /mnt/lolday-samples
```

- [ ] **Step 3: Verify only intended change**

Run:

```bash
git diff charts/lolday/values.yaml
```

Expected: exactly one `-` line and one `+` line; no other changes.

- [ ] **Step 4: Render chart and inspect samples PV**

Run:

```bash
helm template charts/lolday --show-only templates/samples-pv.yaml 2>/dev/null \
  | grep -A2 'hostPath:'
```

Expected: `path: /mnt/lolday-samples` in both samples PV and samples-jobs PV blocks.

- [ ] **Step 5: Commit chart change**

```bash
git add charts/lolday/values.yaml
git commit -m "$(cat <<'EOF'
feat(charts): point samples.hostPath at NFS union mount

Flips samples.hostPath from the legacy /mnt/ssd500g/data/samples
placeholder to /mnt/lolday-samples (mergerfs union of three NFS
sample banks on server14, see spec).

PV/PVC must be re-created out-of-band because spec.hostPath is
immutable on a bound PV; this commit only flips the chart value.

Spec: docs/superpowers/specs/2026-05-12-nfs-dataset-union-mount-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit succeeds; pre-commit hooks pass.

---

### Task 2.2: Drain pods that mount the samples PVC

- [ ] **Step 1: Identify pods currently mounting the samples PVC**

Run:

```bash
kubectl -n lolday get pods -l app.kubernetes.io/component=backend
kubectl -n lolday-jobs get pods 2>&1 | head -10
```

Expected: one backend pod in `lolday`, zero or few in `lolday-jobs`. If detector jobs are running, STOP and wait (or coordinate with their owners).

- [ ] **Step 2: Scale backend Deployment to 0**

Run:

```bash
kubectl -n lolday scale deployment backend --replicas=0
kubectl -n lolday wait --for=delete pod -l app.kubernetes.io/component=backend --timeout=60s
```

Expected: backend pod terminates; no pods remain matching that selector.

- [ ] **Step 3: Confirm no active mounter remains**

Run:

```bash
kubectl -n lolday get pods -l app.kubernetes.io/component=backend
kubectl -n lolday-jobs get pods 2>&1
```

Expected: no pods in `lolday` matching backend; no detector vcjobs running in `lolday-jobs`. If anything still mounts the PVC, do not proceed.

---

### Task 2.3: Delete PVCs and PVs

- [ ] **Step 1: Verify current PV/PVC state**

Run:

```bash
kubectl -n lolday get pvc samples
kubectl -n lolday-jobs get pvc samples
kubectl get pv samples samples-jobs
```

Expected: both PVCs `Bound`; PVs in `Bound` phase with hostPath `/mnt/ssd500g/data/samples`.

- [ ] **Step 2: Delete PVCs first**

Run:

```bash
kubectl -n lolday delete pvc samples
kubectl -n lolday-jobs delete pvc samples
```

Expected: both deletes complete. PVs (Retain policy) remain but transition to `Released`.

- [ ] **Step 3: Delete PVs**

Run:

```bash
kubectl delete pv samples samples-jobs
```

Expected: both deletes complete.

- [ ] **Step 4: Confirm clean state**

Run:

```bash
kubectl get pv 2>&1 | grep -E 'samples|NAME'
kubectl -n lolday get pvc 2>&1 | grep -E 'samples|NAME'
kubectl -n lolday-jobs get pvc 2>&1 | grep -E 'samples|NAME'
```

Expected: `samples` and `samples-jobs` PV/PVCs absent (only headers visible if any other resources exist).

---

### Task 2.4: Helm upgrade

- [ ] **Step 1: Run helm upgrade**

Run:

```bash
bash scripts/deploy.sh
```

Or, if you prefer raw helm:

```bash
helm upgrade lolday charts/lolday/ -n lolday --reuse-values
```

(`deploy.sh` is the project-standard wrapper; use it if Phase 2 of the spec applies.)

Expected: upgrade reports success; samples PV/PVC re-created.

- [ ] **Step 2: Verify new PV/PVC has correct hostPath**

Run:

```bash
kubectl get pv samples samples-jobs -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.hostPath.path}{"\n"}{end}'
kubectl -n lolday get pvc samples -o jsonpath='{.status.phase}{"\n"}'
kubectl -n lolday-jobs get pvc samples -o jsonpath='{.status.phase}{"\n"}'
```

Expected:

```
samples /mnt/lolday-samples
samples-jobs /mnt/lolday-samples
Bound
Bound
```

---

### Task 2.5: Scale backend back up

- [ ] **Step 1: Scale backend Deployment back to 1**

Run:

```bash
kubectl -n lolday scale deployment backend --replicas=1
kubectl -n lolday rollout status deployment/backend --timeout=120s
```

Expected: rollout completes successfully; new pod `Running` and `Ready 1/1`.

- [ ] **Step 2: Verify backend logs show clean startup**

Run:

```bash
kubectl -n lolday logs -l app.kubernetes.io/component=backend --tail=30
```

Expected: `Application startup complete.` and `Uvicorn running on http://0.0.0.0:8000`. No tracebacks. If you see startup errors mentioning `samples`, jump to Rollback (§ end of plan).

---

## Phase 3: End-to-end verification

### Task 3.1: Backend pod sees the union view

- [ ] **Step 1: Inspect mounted samples directory inside the backend pod**

Run:

```bash
POD=$(kubectl -n lolday get pod -l app.kubernetes.io/component=backend -o jsonpath='{.items[0].metadata.name}')
kubectl -n lolday exec "$POD" -- ls /mnt/samples/ | sort | head -5
kubectl -n lolday exec "$POD" -- ls /mnt/samples/ | wc -l
```

Expected: prints `00 01 02 03 04` and count `256`.

- [ ] **Step 2: Spot-check sample resolution from inside the pod for each bank**

Run:

```bash
POD=$(kubectl -n lolday get pod -l app.kubernetes.io/component=backend -o jsonpath='{.items[0].metadata.name}')
for prefix in 01 02 03; do
  fname=$(kubectl -n lolday exec "$POD" -- ls /mnt/samples/$prefix/ | head -1)
  echo "prefix=$prefix  file=$fname"
  kubectl -n lolday exec "$POD" -- stat /mnt/samples/$prefix/$fname | head -2
done
```

Expected: each iteration prints a SHA-256-named file and `stat` succeeds (Size: > 0).

---

### Task 3.2: Synthetic DatasetConfig upload + spot-check

This validates that lolday's `parse_csv` + `spot_check_samples` see the union via `SAMPLES_LOCAL_ROOT`.

**Files:**

- Create (temp): `/tmp/synthetic_dataset.csv` (in your shell on server30)

- [ ] **Step 1: Build a 3-sample synthetic CSV referencing each bank**

Run:

```bash
{
  echo 'file_name,label'
  # 1 sample from 2025
  ls /mnt/server14/dataset/nict202503/nictMalware/0a/ | head -1 \
    | awk '{print $1",Malware"}'
  # 1 sample from 2024
  ls /mnt/server14/dataset/nict202403/nictMalware/0b/ | head -1 \
    | awk '{print $1",Malware"}'
  # 1 sample from benignware
  ls /mnt/server14/dataset/benignware/data/0c/ | head -1 \
    | awk '{print $1",Benign"}'
} > /tmp/synthetic_dataset.csv
cat /tmp/synthetic_dataset.csv
```

Expected: 4 lines (header + 3 SHA-256 + label rows), no blanks.

- [ ] **Step 2: Run spot-check directly from backend pod (avoids API auth complexity)**

Run:

```bash
POD=$(kubectl -n lolday get pod -l app.kubernetes.io/component=backend -o jsonpath='{.items[0].metadata.name}')
kubectl -n lolday cp /tmp/synthetic_dataset.csv "$POD:/tmp/synthetic_dataset.csv"
kubectl -n lolday exec "$POD" -- sh -c 'cd /app && PYTHONPATH=/app uv run python - <<PY
from pathlib import Path
from app.services.dataset import parse_csv, spot_check_samples
csv_content = Path("/tmp/synthetic_dataset.csv").read_text()
parsed = parse_csv(csv_content)
print("Parsed sample_count:", parsed.sample_count)
print("Labels:", parsed.label_distribution)
result = spot_check_samples(
    file_names=parsed.file_names,
    samples_root=Path("/data"),
    sample_size=len(parsed.file_names),
)
print(f"Spot-check: checked={result.checked} missing={result.missing}")
assert result.missing == 0, f"Missing samples: {result.missing}"
print("PASS")
PY'
```

Expected:

```
Parsed sample_count: 3
Labels: {'Malware': 2, 'Benign': 1}
Spot-check: checked=3 missing=0
PASS
```

The path `/data` matches `SAMPLES_LOCAL_ROOT=/data` (backend env); inside the pod, the samples PVC is mounted at `/data` (via the deployment template).

If `missing > 0`: STOP. The union mount is not visible to the backend pod the way we expected. Re-verify Task 2.4 / 2.5.

---

### Task 3.3: Tail backend logs for any sample-related errors

- [ ] **Step 1: Inspect last 200 lines of backend log**

Run:

```bash
kubectl -n lolday logs -l app.kubernetes.io/component=backend --tail=200 \
  | grep -iE 'error|samples|spot.?check|enoent' | head -30
```

Expected: empty output (no errors). If you see `ENOENT` referring to `/data/<prefix>/<sha256>`, jump to Rollback.

---

## Phase 4: Documentation

### Task 4.1: Update `docs/architecture.md` §6 (storage)

**Files:**

- Modify: `docs/architecture.md` (storage section)

- [ ] **Step 1: Read the current §6 storage section**

Run:

```bash
grep -n '^## 6\|^### 6\.' docs/architecture.md
```

Then read the matching line range (use the Read tool with the offset/limit returned).

- [ ] **Step 2: Add or revise the samples paragraph**

Insert (or replace existing samples description) with the following paragraph in the appropriate sub-section of §6:

```markdown
**Detector samples** live on an NFS share exported by server14
(`140.118.155.14:/mnt/hdd4t/dataset`), mounted on server30 at
`/mnt/server14/dataset` (NFSv4.2, `ro`, `nofail`, `_netdev`). Three
sample banks are combined into a single read-only view via a
mergerfs FUSE union at `/mnt/lolday-samples`, with branch order
`nict202503 > nict202403 > benignware` encoding dedup priority on
SHA-256 collision (2025 wins). The chart's `samples.hostPath` points
at the union, so backend and detector job pods see one flat
`<root>/<prefix>/<sha256>` layout regardless of which underlying
bank a sample physically lives in. Adding a new sample bank means
mounting the source on server30 and appending it to the mergerfs
`lowerdir` list (see `docs/runbooks/add-nfs-dataset.md`); no chart
or backend change.

Spec: `docs/superpowers/specs/2026-05-12-nfs-dataset-union-mount-design.md`.
```

- [ ] **Step 3: Verify the section reads coherently**

Run:

```bash
grep -n -A20 '^## 6\|^### 6\.' docs/architecture.md | head -60
```

Inspect the result manually to confirm continuity.

---

### Task 4.2: Update `docs/operations.md` — add NFS sources section

**Files:**

- Modify: `docs/operations.md` (append a new top-level section before the final "Server access" section, or wherever feels coherent)

- [ ] **Step 1: Read the existing operations.md structure**

Run:

```bash
grep -n '^## ' docs/operations.md
```

- [ ] **Step 2: Append the NFS sources section**

Add (just before the final `## Server access` section):

```markdown
## NFS dataset sources

Detector samples are NOT stored on server30's local SSD. They are
served from server14 via NFS and combined into a single `samples_root`
view via mergerfs.

| Path                    | Backing                                                                      |
| ----------------------- | ---------------------------------------------------------------------------- |
| `/mnt/server14/dataset` | NFSv4.2 from `140.118.155.14:/mnt/hdd4t/dataset` (`ro`, `nofail`, `_netdev`) |
| `/mnt/lolday-samples`   | mergerfs union — branches ordered: 2025 → 2024 → benignware                  |

The chart's `samples.hostPath` (`charts/lolday/values.yaml`) points
at `/mnt/lolday-samples`. Backend and job pods mount the resulting
PVC at `/mnt/samples` (`SAMPLES_ROOT`).

**Branch order = dedup priority.** A sample SHA-256 present in
multiple banks resolves to the file from the first matching branch
(currently 2025 wins over 2024 wins over benignware).

**Adding a new dataset bank** — see `docs/runbooks/add-nfs-dataset.md`.
The short version:

1. Mount the new NFS source on server30 at `/mnt/<src>` (`ro`, `nofail`, `_netdev`)
2. Edit `/etc/fstab` mergerfs line — insert the new path at the
   priority position you want (left = higher priority)
3. `sudo umount /mnt/lolday-samples && sudo mount -a`
4. Upload the new bank's CSV as a fresh lolday `DatasetConfig`

No chart or backend change required.

**Removing a bank** — reverse: edit fstab to drop the path, remount.
Existing `DatasetConfig` rows that referenced samples in the removed
bank will fail `spot_check_samples` on next job submission, which is
the desired behaviour (loud failure beats silent missing-sample
training data).
```

- [ ] **Step 3: Verify the section was added cleanly**

Run:

```bash
grep -n '^## ' docs/operations.md
```

Expected: a new `## NFS dataset sources` line between the previous sections.

---

### Task 4.3: Create runbook `docs/runbooks/add-nfs-dataset.md`

**Files:**

- Create: `docs/runbooks/add-nfs-dataset.md`

- [ ] **Step 1: Confirm runbook doesn't exist yet**

Run:

```bash
ls -la docs/runbooks/add-nfs-dataset.md 2>&1
```

Expected: `No such file or directory`.

- [ ] **Step 2: Write the runbook**

Create `docs/runbooks/add-nfs-dataset.md` with content:

````markdown
# Adding a new NFS-backed sample bank

Operational checklist for onboarding a new dataset (NFS sample bank) into the
lolday `samples_root` view, without copying data and without backend / chart
redeploy.

**Pre-requisites:**

- The new dataset must already be exported from some NFS server reachable
  from server30
- The internal directory structure of the dataset must be lolday's
  hex-prefix convention: `<source>/<sha256[:2]>/<sha256>` flat files
- You have sudo on server30
- You know what priority the new bank should have relative to existing
  banks (left = higher in the mergerfs branch list)

## Procedure

### 1. Verify the new NFS source is reachable

```bash
ping -c 2 -W 2 <nfs-server-ip>
```

### 2. Add the NFS mount to fstab

If the NFS source is on a server already used (e.g. `140.118.155.14`),
mount a new sub-directory the same way as the existing one. If it is a
new server, follow `docs/runbooks/deploy.md` SSH-safety discipline.

Example fstab line:

```
<nfs-server-ip>:/path/on/server  /mnt/<short-name>  nfs  ro,_netdev,nofail,soft,timeo=30,retrans=2  0  0
```

Activate:

```bash
sudo mkdir -p /mnt/<short-name>
sudo mount -a
mountpoint /mnt/<short-name> && echo MOUNTED
ls /mnt/<short-name>/<source-subdir> | head -3
```

The last `ls` should show `00 01 02 ...` hex-prefix sub-directories. If
not, the dataset is not in lolday format and **cannot** be onboarded via
this runbook — file an issue first.

### 3. Edit `/etc/fstab` mergerfs line — insert the new source path

Open `/etc/fstab` and find the line for `/mnt/lolday-samples`. The first
field is colon-separated source paths. Insert the new source path at the
priority position you want — leftmost = highest priority for dedup.

Example: to add a hypothetical `nict202605/nictMalware` with the highest
priority:

```diff
- /mnt/server14/dataset/nict202503/nictMalware:/mnt/server14/dataset/nict202403/nictMalware:/mnt/server14/dataset/benignware/data ...
+ /mnt/server14/dataset/nict202605/nictMalware:/mnt/server14/dataset/nict202503/nictMalware:/mnt/server14/dataset/nict202403/nictMalware:/mnt/server14/dataset/benignware/data ...
```

### 4. Remount the union

```bash
sudo umount /mnt/lolday-samples
sudo mount -a
mountpoint /mnt/lolday-samples
ls /mnt/lolday-samples/ | wc -l   # expect 256
```

The `umount` of mergerfs should succeed instantly because the union is
read-only — no dirty pages to flush. If any backend pod is mid-read, it
will see a brief `ESTALE`; the backend reconciler retries automatically.

For zero-downtime swap (advanced): use a systemd `.mount` unit with
`Restart=on-failure` instead of fstab; out of scope here.

### 5. Verify the new bank shows up under the union

Pick a SHA-256 known to live only in the new bank:

```bash
NEW_SHA=$(ls /mnt/<short-name>/<source-subdir>/00/ | head -1)
echo "Picked: $NEW_SHA"
stat /mnt/lolday-samples/00/$NEW_SHA   # should succeed
```

### 6. Upload the new bank's CSV as a lolday `DatasetConfig`

Use the lolday UI (`Datasets → New`) and upload the CSV that ships with
the bank (e.g. `nictMalware_info.csv`). Spot-check will pass because
every CSV row's SHA-256 resolves under the union.

For cross-bank training datasets, build a deduped combined CSV in pandas
(see spec §5.3) and upload that.

## Removing a bank

The reverse: remove the path from the mergerfs fstab line, remount.

Any `DatasetConfig` row whose CSV references samples in the removed bank
will fail `spot_check_samples` on next job submission. This is the
intended behaviour — loud failure beats silent missing-sample training
data.

## Troubleshooting

| Symptom                                                   | Likely cause                                      | Action                                                                                       |
| --------------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `mount.fuse.mergerfs: command not found` after fstab edit | mergerfs uninstalled                              | `sudo apt-get install fuse mergerfs`                                                         |
| Union mount empty after remount                           | typo in fstab branch path                         | `findmnt --verify /etc/fstab`; restore from `/etc/fstab.bak-*`                               |
| Backend pod `ENOENT` on a known sample                    | union not picking up the source's `00..ff` layout | verify the source has the right internal structure (`ls /mnt/<short-name>/<source-subdir>/`) |
| Cross-bank duplicate served from wrong bank               | branch order wrong in fstab                       | edit fstab branch order, `umount`/`mount -a`                                                 |

## Related

- Spec: `docs/superpowers/specs/2026-05-12-nfs-dataset-union-mount-design.md`
- Architecture: `docs/architecture.md` §6 (storage)
- Operations quick-ref: `docs/operations.md` (NFS dataset sources)
````

- [ ] **Step 3: Verify file written**

Run:

```bash
wc -l docs/runbooks/add-nfs-dataset.md
head -5 docs/runbooks/add-nfs-dataset.md
```

Expected: ~90-130 lines; first line is `# Adding a new NFS-backed sample bank`.

---

### Task 4.4: Commit docs changes

- [ ] **Step 1: Inspect what's staged vs unstaged**

Run:

```bash
git status
git diff --stat
```

Expected: 3 files in working tree —

- modified `docs/architecture.md`
- modified `docs/operations.md`
- new `docs/runbooks/add-nfs-dataset.md`

- [ ] **Step 2: Stage and commit**

```bash
git add docs/architecture.md docs/operations.md docs/runbooks/add-nfs-dataset.md
git commit -m "$(cat <<'EOF'
docs: NFS dataset union mount — architecture, ops, runbook

- architecture.md §6: describe NFS-backed samples_root with mergerfs union
- operations.md: add NFS dataset sources section with branch-priority semantics
- runbooks/add-nfs-dataset.md: new runbook for onboarding additional banks

Spec: docs/superpowers/specs/2026-05-12-nfs-dataset-union-mount-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit succeeds; pre-commit hooks pass (prettier may reformat — re-stage + retry once if it does, per CLAUDE.md commit discipline).

---

## Phase 5: Push and open PR (operator decides)

> Only run this phase after the operator explicitly authorises pushing to origin.

### Task 5.1: Push branch

- [ ] **Step 1: Push the branch upstream**

```bash
git push -u origin docs/nfs-dataset-union-mount-spec
```

Expected: branch created on origin; tracking set.

### Task 5.2: Open PR

- [ ] **Step 1: Open PR via gh CLI**

```bash
gh pr create --title "feat(samples): NFS dataset union mount via mergerfs" \
  --body "$(cat <<'EOF'
## Summary

- New mergerfs FUSE union mount at `/mnt/lolday-samples` combines three
  NFS-backed sample banks into a single lolday `samples_root` view
- Chart `samples.hostPath` flips to the union (one-line `values.yaml`
  change; PV/PVC re-create required, see spec §5.2)
- Zero copy, zero backend code change

## Test plan

- [x] mergerfs / fuse installed on server30, mount active (Phase 1)
- [x] PV/PVC re-created against `/mnt/lolday-samples`, backend reads
      union view (Phase 2-3)
- [x] Synthetic CSV with samples from all three banks passes
      `spot_check_samples` (Phase 3)
- [ ] (post-merge) End-to-end eval job submitted by operator against the
      new union; verify training reads samples without I/O error

Spec: docs/superpowers/specs/2026-05-12-nfs-dataset-union-mount-design.md
Plan: docs/superpowers/plans/2026-05-12-nfs-dataset-union-mount.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL returned. Paste it into the operator's session.

---

## Rollback procedures

### Phase 1 rollback (mount active but breaks something)

```bash
# Restore fstab from backup
sudo cp /etc/fstab.bak-<timestamp> /etc/fstab
sudo umount /mnt/lolday-samples
sudo mount -a
# Verify backend pod still has working sample mount (will be /mnt/ssd500g/data/samples placeholder, harmless)
```

### Phase 2 rollback (after PV/PVC delete, before successful upgrade)

```bash
# Revert the chart change locally
git revert HEAD                # if you committed Task 2.1
# OR
git checkout charts/lolday/values.yaml
bash scripts/deploy.sh         # recreate samples PV at old hostPath /mnt/ssd500g/data/samples
kubectl -n lolday scale deployment backend --replicas=1
```

### Phase 2 rollback (after successful upgrade, want to undo)

```bash
# Reverse the cutover
kubectl -n lolday scale deployment backend --replicas=0
kubectl -n lolday delete pvc samples
kubectl -n lolday-jobs delete pvc samples
kubectl delete pv samples samples-jobs
git revert HEAD                # reverts the values.yaml chart commit
bash scripts/deploy.sh
kubectl -n lolday scale deployment backend --replicas=1
# Union mount stays mounted but is unused — optionally umount + remove fstab entry
```

### Discord notify

If Phase 2 takes longer than 5 minutes, post a brief message in Spidey
Service Alerts (`1495967957992603788`) saying the platform is down for
samples-PV cutover; reply once `kubectl rollout status` returns success.

---

## Post-implementation

After PR merge:

- [ ] Verify `Spidey Service Alerts` did not produce any `KubePodCrashLooping`
      events for backend during the cutover window
- [ ] Watch for any unusual `BACKEND_ERRORS{stage=...}` Prometheus counter
      changes in the 24h after merge
- [ ] If everything stable: delete the old `/mnt/ssd500g/data/samples`
      placeholder directory (it's empty, but it's clutter):
      `sudo rmdir /mnt/ssd500g/data/samples` (only if `ls` returns empty)
