# NFS Dataset Union Mount — Design Specification

> **Created 2026-05-12**. Trigger: operator mounted a new NFS share
> `140.118.155.14:/mnt/hdd4t/dataset` (server14, ISLab shared storage) onto
> server30 at `/mnt/server14/dataset`. The share carries three pre-organised
> sample banks (`benignware/data/`, `nict202403/nictMalware/`,
> `nict202503/nictMalware/`) that the operator wants to surface to lolday
> training jobs **without copying** and **without backend code changes**.

> **This spec answers**: how to expose three independent NFS sample banks as
> a single lolday `samples_root` view, with zero copy, dedup priority
> (2025 > 2024 > benignware), and no lolday backend code change.

## 1. Overview

Lolday's `samples_root` is a single directory under which all detector samples
sit at `<root>/<sha256[:2]>/<sha256>` (a flat hex-prefix layout inherited from
the upxelfdet convention). The current production `samples.hostPath` points at
`/mnt/ssd500g/data/samples` — an empty placeholder on the local SSD.

The new NFS share at `/mnt/server14/dataset` contains three sample banks that
each internally use the same hex-prefix layout:

```
/mnt/server14/dataset/
├── benignware/data/         (256 sub-dirs 00..ff)
├── nict202403/nictMalware/  (256 sub-dirs 00..ff)
└── nict202503/nictMalware/  (256 sub-dirs 00..ff)
```

Since lolday identifies samples by SHA-256 alone (no dataset-name dimension on
the filesystem path), the three banks can be **merged into a single view** —
SHA-256 collision across banks is cryptographically negligible, and where the
same binary legitimately appears in two banks the union must deterministically
prefer the newer one (2025 over 2024).

This spec proposes:

| Layer           | Change                                                                                                                        | Reason                                                                                                         |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| **Host**        | `mergerfs` FUSE union mount at `/mnt/lolday-samples`, branches ordered `nict202503 > nict202403 > benignware`                 | Zero-copy union view; branch order drives dedup priority                                                       |
| **Chart**       | `samples.hostPath: /mnt/lolday-samples` (one-line change in `charts/lolday/values.yaml`)                                      | Existing PV/PVC unchanged; backend / job pods transparently see the union                                      |
| **Backend**     | No change                                                                                                                     | `_sample_path(root, sha256)` already does `root / sha256[:2] / sha256`; the union root satisfies this contract |
| **Dataset CSV** | Per-bank CSVs upload as separate `DatasetConfig` rows; cross-bank training uses a pre-deduped combined CSV (user-side pandas) | lolday's `DatasetConfig` already stores CSV content verbatim; union doesn't constrain CSV semantics            |

## 2. Authorization

Operator authorised the following at the 2026-05-12 brainstorming stage:

- **No data copies** — link or union view only; SSD copy of NFS data is
  explicitly rejected
- **Use mainstream tooling** — mergerfs is the NAS-ecosystem standard for
  read-only union of multiple backing stores
- **Cross-bank training is in scope** — a single training CSV may reference
  samples from multiple banks; union view must serve them uniformly
- **2025 takes precedence over 2024** on SHA-256 collisions
- **lolday backend code is not in scope** for this change — the union must
  be transparent to backend / job pods
- **Breaking changes to the chart's `samples.hostPath` value are permitted**
  (no dual-source migration phase needed; the existing path holds only an
  empty placeholder)

## 3. Scope

### 3.1 In scope

**Host-level mount**

1. Install `fuse` and `mergerfs` packages on server30 (apt-managed, no
   user-local install — these are kernel/FUSE-adjacent tools)
2. Create mount point `/mnt/lolday-samples` (empty directory; mergerfs will
   overlay the union view)
3. Add `/etc/fstab` entry for mergerfs union with branches ordered
   `nict202503 > nict202403 > benignware`
4. Validate union view by spot-checking sample resolution (`stat
/mnt/lolday-samples/00/<known-sha256>` succeeds and returns content from
   the expected branch per the dedup policy)

**Chart change**

5. `charts/lolday/values.yaml`: `samples.hostPath: /mnt/ssd500g/data/samples`
   → `/mnt/lolday-samples` (one-line)
6. `helm upgrade` (or `scripts/deploy.sh`) to reroll backend + job pod
   templates; existing `samples` PV/PVC are recreated with the new host path
   (PV is ReadOnlyMany hostPath, no data migration semantics involved)

**Verification**

7. Backend pod sees union view: `kubectl exec backend -- ls
/mnt/samples/00 | wc -l` returns a count consistent with the union (sum
   of distinct SHA-256 prefixes across all three banks at `00/`)
8. `spot_check_samples` on a small synthetic CSV referencing samples from
   each of the three banks passes when the CSV is uploaded as a
   `DatasetConfig`

**Documentation**

9. `docs/architecture.md` §6 (storage) updated to describe the new
   `samples_root` topology (NFS-backed union mount; samples are no longer
   on local SSD)
10. `docs/operations.md` adds a section on NFS dataset sources, mergerfs
    branch ordering convention, and how to add a new dataset source
11. `docs/runbooks/add-nfs-dataset.md` (new) — how to onboard an additional
    sample bank (mount NFS share, add to mergerfs `lowerdir` list,
    `mount -o remount`)

### 3.2 Out of scope

- **LDAP / FreeIPA centralised identity** — the cross-server GID misalignment
  cosmetic was resolved separately by manual GID alignment (server30
  diskaccess GID 1001 → 1002 to match server14); centralised identity is a
  lab-wide infrastructure project, not driven by this dataset onboarding
- **NFSv4 sec=krb5** — requires KDC infrastructure ISLab does not yet have;
  lab-level decision, separate proposal needed
- **Per-dataset bind mount + backend `dataset_name` path parameter** — would
  require changes to `_sample_path` signature and `DatasetConfig` schema;
  considered and rejected (see §6.2)
- **OverlayFS lower-only mount** — kernel-native alternative; rejected for
  NFS-backed unions due to inode stability caveats (see §6.1)
- **Migrating existing samples off `/mnt/ssd500g/data/samples`** — the
  current path holds only an empty placeholder; no data to migrate
- **K8s NFS CSI driver / dynamic NFS PV provisioning** — operationally
  heavier than the operator's need; hostPath + union mount is sufficient
  for the single-node server30 deployment
- **Automatic CSV-level dedup in `parse_csv`** — the user/operator
  pre-dedupes combined CSVs in pandas before upload; backend-side dedup
  would conflict with the explicit "lolday does not transform CSV content"
  invariant

## 4. Background

### 4.1 NFS share layout (after Phase 0 mount, already in place)

```
/mnt/server14/dataset/  (root:diskaccess, ro, NFSv4.2 sec=sys)
├── AEs/                       # out of scope (legacy adversarial examples)
├── archive_files/             # out of scope
├── Malware202503_info.csv     # bundle-level CSV
├── benignware/
│   ├── benignware_info.csv
│   └── data/                  # ← bank: 256 hex sub-dirs
├── nict202403/
│   ├── Malware202403_info.csv
│   ├── nictMalware/           # ← bank: 256 hex sub-dirs
│   └── nictMalwareReport/     # out of scope (per-sample analysis reports)
└── nict202503/
    ├── Malware202503.csv
    ├── nictMalware/           # ← bank: 256 hex sub-dirs
    └── nictMalwareReport/
```

Three "banks" are union targets: `benignware/data/`,
`nict202403/nictMalware/`, `nict202503/nictMalware/`.

The CSVs (`benignware_info.csv`, `Malware202403_info.csv`,
`Malware202503.csv`) carry `file_name,label[,family]` rows where `file_name`
is the sample SHA-256. These are uploaded individually as
`DatasetConfig` entries by the operator; the bundle-level
`Malware202503_info.csv` at the dataset root is treated as a superset CSV
(operator's choice whether to upload).

### 4.2 Lolday's sample-resolution contract

`backend/app/services/dataset.py:_sample_path`:

```python
def _sample_path(samples_root: Path, file_name: str) -> Path:
    prefix = file_name[:2]
    return samples_root / prefix / file_name
```

The function depends on a single `samples_root` argument that resolves to a
directory containing `00..ff` sub-directories of SHA-256-named samples.
`DatasetConfig` (`backend/app/models/dataset.py`) has no `dataset_name`
column on the filesystem-path side; the dataset boundary lives inside the
uploaded CSV string only.

This is the contract the union mount must satisfy: provide a single
directory whose `00..ff` children contain every reachable SHA-256 from the
three banks, with deterministic resolution under collision.

### 4.3 Why mergerfs

mergerfs is a FUSE-based union filesystem actively maintained since 2014,
used as the de-facto union/storage-pool layer in OpenMediaVault, Unraid
alternatives, and JBOD NAS builds. It is purpose-built for unioning
multiple backing stores with explicit policy control (search / action /
create), in contrast with OverlayFS, whose mainstream use case is container
image layering. For a read-only multi-backing-store union over NFS,
mergerfs is the mainstream answer.

Key policies for this design:

- `category.search=ff` (first-found, default): branches are searched in
  order; the first match wins. With `nict202503` listed first, a SHA-256
  present in both 2025 and 2024 banks resolves to the 2025 file.
- `category.action=ro`: union is forced read-only; writes return EROFS even
  if the underlying NFS were mounted rw.
- `use_ino`: inodes propagate from the underlying backing store, giving K8s
  hostPath consumers stable inode numbers.

## 5. Design

### 5.1 Mergerfs mount

`/etc/fstab` entry:

```
/mnt/server14/dataset/nict202503/nictMalware:/mnt/server14/dataset/nict202403/nictMalware:/mnt/server14/dataset/benignware/data  /mnt/lolday-samples  fuse.mergerfs  allow_other,use_ino,category.action=ro,category.search=ff,minfreespace=0,nofail,_netdev  0  0
```

Option breakdown:

| Option               | Purpose                                                |
| -------------------- | ------------------------------------------------------ |
| `category.action=ro` | Force read-only; any modification returns EROFS        |
| `category.search=ff` | First-found search policy; branch order = priority     |
| `use_ino`            | Use backing inode numbers (K8s / cache friendly)       |
| `allow_other`        | Non-root processes (K8s container runtime) can read    |
| `minfreespace=0`     | Skip free-space check (irrelevant for read-only)       |
| `nofail`             | Boot does not block if NFS backing is unavailable      |
| `_netdev`            | Wait for network-online.target; correct shutdown order |

The branch order encodes the dedup policy. Future additions are appended at
the appropriate priority position (newer datasets go to the front of the
list).

### 5.2 K8s chart change

`charts/lolday/values.yaml` (one-line):

```yaml
samples:
  hostPath: /mnt/lolday-samples # was /mnt/ssd500g/data/samples
```

The existing `templates/samples-pv.yaml` already declares `accessModes:
[ReadOnlyMany]` and both the `samples` PV (in `lolday` ns) and the parallel
`samples-jobs` PV (in `lolday-jobs` ns) bind to this hostPath.

**PV recreate is required**: `spec.hostPath` is not in the K8s-documented
mutable PV field set (`capacity`, `persistentVolumeReclaimPolicy`,
`storageClassName`). A plain `helm upgrade` therefore cannot mutate the
existing PV's hostPath in-place; the operator must delete the existing
`samples` + `samples-jobs` PV/PVC pair before the upgrade re-creates them
against the new value. Sequence:

1. Edit `samples.hostPath` in `values.yaml`
2. Drain pods that mount the `samples` PVC (backend Deployment +
   any running detector jobs in `lolday-jobs`) — operator decision whether
   to wait for in-flight jobs to drain or kill them
3. `kubectl -n lolday delete pvc samples` and `kubectl -n lolday-jobs
delete pvc samples` (PVs are `Retain` so they survive)
4. `kubectl delete pv samples samples-jobs`
5. `helm upgrade` (or `bash scripts/deploy.sh`) — PV/PVC are re-rendered
   with the new hostPath
6. Backend Deployment auto-rolls (new ReplicaSet) and re-mounts the new
   PVC; detector job submissions in `lolday-jobs` proceed normally

Backend and job pods continue to mount the `samples` PVC at the same
container-side path (backend env `SAMPLES_ROOT=/mnt/samples`,
`SAMPLES_LOCAL_ROOT=/data` — both unaffected; only the host backing
changes).

### 5.3 Dataset CSV upload strategy

The operator uploads CSVs in two modes:

**Single-bank dataset** (the common case):

- `benignware_info.csv` → `DatasetConfig` row "benignware"
- `Malware202403_info.csv` → `DatasetConfig` row "nictMalware-2024"
- `Malware202503.csv` → `DatasetConfig` row "nictMalware-2025"

Spot-check passes because every CSV row's SHA-256 resolves under the union
view (the file lives in exactly one of the three branches).

**Cross-bank combined dataset** (optional, operator-driven):

The operator builds a deduped combined CSV in pandas before upload, e.g.:

```python
import pandas as pd
combined = pd.concat([
    pd.read_csv("/mnt/server14/dataset/nict202503/Malware202503.csv"),
    pd.read_csv("/mnt/server14/dataset/nict202403/Malware202403_info.csv"),
    pd.read_csv("/mnt/server14/dataset/benignware/benignware_info.csv"),
])
combined = combined.drop_duplicates(subset="file_name", keep="first")
combined.to_csv("/tmp/combined.csv", index=False)
```

`keep="first"` mirrors the mergerfs `category.search=ff` branch ordering —
filesystem-layer dedup and CSV-layer dedup stay semantically aligned.

Lolday backend does not need to know whether a CSV is single-bank or
combined; both resolve identically through the union view.

### 5.4 Onboarding a new dataset (future)

Adding a fourth bank (e.g. a 2026 NICT dump):

1. Mount the new NFS share (or sub-directory) on server30 at `/mnt/<src>`
2. Add the path at the appropriate priority position in the mergerfs
   `lowerdir` list in `/etc/fstab`
3. `sudo umount /mnt/lolday-samples && sudo mount -a` (or `mount -o
remount`; mergerfs supports it)
4. Upload the new bank's CSV as a fresh `DatasetConfig`

No chart change, no backend redeploy, no data copy.

This procedure lives in `docs/runbooks/add-nfs-dataset.md`.

## 6. Alternatives considered

### 6.1 OverlayFS lower-only (kernel-native union)

OverlayFS (Linux kernel ≥ 5.11) supports lower-only read-only mounts via
multiple `lowerdir` entries. server30 kernel 6.8 has full support.

Mount form:

```
mount -t overlay overlay -o lowerdir=<2025>:<2024>:<benignware>,redirect_dir=on /mnt/lolday-samples
```

Rejected because:

- OverlayFS's mainstream use case is container image layering, not NAS
  union; community NFS-as-lower documentation is sparse and inode
  stability across NFS reconnects is a known caveat
- mergerfs is purpose-built for this scenario with explicit policy controls
  the operator may need later (different search / action policies per
  category)
- The FUSE overhead of mergerfs is negligible for ML training read
  patterns (sequential per-sample reads dominated by GPU forward-pass
  compute, not I/O)
- Operationally, mergerfs branches are edited in one fstab line; OverlayFS
  re-mount semantics for adding a `lowerdir` are clumsier

### 6.2 Per-dataset bind mount + backend `dataset_name` path parameter

Alternative architecture: each bank bind-mounted to its own subdirectory
under `/mnt/lolday-samples/<bank_name>/`; backend `_sample_path` extended to
accept a `dataset_name` parameter and resolve to `samples_root / dataset_name
/ prefix / sha256`; `DatasetConfig` schema gains a `dataset_subdir` column.

Rejected because:

- Breaks the "lolday backend not in scope" authorisation constraint —
  requires backend code change, DB migration, and matching frontend
  updates to surface `dataset_subdir`
- Conflicts with cross-bank combined-CSV training (the operator's stated
  in-scope use case) — combined CSVs would need rows tagged with
  `dataset_subdir` per-row, which is a CSV schema change cascading to
  upxelfdet / detector conventions outside lolday
- Adds a dataset-boundary dimension on the filesystem path that lolday's
  current contract intentionally avoids (datasets are CSV-level constructs,
  not filesystem-level)

### 6.3 Copy samples to local SSD

Standard "everything on fast local disk" pattern, would require ~150 GB+
copy across the three banks (NICT 2024 + 2025 + benignware combined).

Rejected because:

- Operator explicitly required no-copy in the brainstorming stage
- NFS read throughput on the ISLab network (server14 ↔ server30, same
  switch) saturates the GPU training pipeline for the detectors lolday
  runs; SSD-local copy provides no measurable training speed-up
- Maintenance burden: every dataset version bump requires a re-sync

## 7. Risks and mitigations

| Risk                                                                          | Likelihood                             | Impact                                                            | Mitigation                                                                                                                                                                                                                                                                                                               |
| ----------------------------------------------------------------------------- | -------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| mergerfs FUSE process crashes; union mount becomes stale                      | Low                                    | Backend / jobs see `Transport endpoint is not connected` on reads | Plain fstab mounts do not auto-restart on FUSE crash — operator must `umount -lf /mnt/lolday-samples && mount -a`. Future work (§10): convert to a systemd `.mount` unit with `Restart=on-failure` and dependent `.target` for proper supervision. `nofail` on fstab keeps boot unblocked even if union is stale at boot |
| server14 NFS server outage                                                    | Medium (lab infra outside our control) | Union mount returns I/O errors on read; affected jobs fail        | `soft,timeo=30,retrans=2` on the underlying NFS mount (already configured) prevents indefinite hang; Alertmanager `KubePodCrashLooping` would fire if backend / job pods read-fail repeatedly                                                                                                                            |
| Mergerfs branch order edit error breaks dedup priority                        | Low                                    | Wrong-version sample served (2024 wins over 2025)                 | fstab line documented in runbook with explicit priority comment; verification step `stat <known-cross-bank-sample>` post-mount                                                                                                                                                                                           |
| Operator forgets to add new dataset to mergerfs branch list (only mounts NFS) | Medium                                 | New samples invisible to lolday despite NFS being mounted         | Runbook is checklist-driven; CSV upload spot-check fails loudly if SHA-256 misses, catching this at dataset registration time                                                                                                                                                                                            |
| K8s container UID does not match NFS file permissions                         | Low                                    | Read denied                                                       | NFS files are `r-xr-xr-x` (world-readable); container UID is irrelevant for read-only access                                                                                                                                                                                                                             |

## 8. Testing / verification plan

### 8.1 Host-level

1. After fstab edit: `sudo mount -a` succeeds without errors
2. `mountpoint /mnt/lolday-samples` returns success
3. `mount | grep mergerfs` shows the union with all three branches
4. `ls /mnt/lolday-samples/ | head -3` returns `00 01 02 ...` (256
   hex-prefix directories visible as the union)
5. Pick one SHA-256 known to exist only in `nict202503/nictMalware/<XX>/`;
   `stat /mnt/lolday-samples/<XX>/<sha256>` succeeds. Repeat for `nict202403`
   and `benignware`. Confirm all three resolve under the union.
6. If a cross-bank duplicate SHA-256 exists, verify the `stat` resolves
   from `nict202503`'s path (use `getfattr -n user.mergerfs.fullpath` or
   compare inode against branch inodes)

### 8.2 K8s-level

7. After `helm upgrade`: `kubectl -n lolday get pv samples` shows hostPath
   `/mnt/lolday-samples`
8. `kubectl -n lolday exec backend-* -- ls /mnt/samples/ | head -3`
   returns the same 256-entry listing
9. Upload a small synthetic test `DatasetConfig` with 5 SHA-256 rows
   (1 from each bank + 2 known-good); spot-check passes
10. Trigger a synthetic eval job referencing 3-5 samples spanning all three
    banks; job reads samples without I/O error

### 8.3 Regression

11. Pre-existing detector builds and jobs (from before the cutover) re-run
    if any historical CSVs reference samples that **are not in any bank** —
    those must fail spot-check loudly; if they reference samples that exist
    in one of the new banks, they succeed transparently

## 9. Implementation phases

A single-PR change covering host + chart + docs. Phases are sequential —
each depends on the previous:

| Phase          | Deliverable                                                                                                                                   |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **P1: Host**   | apt install fuse mergerfs; mkdir mount point; fstab edit; mount -a; spot-check sample resolution from each of the three banks                 |
| **P2: Chart**  | `samples.hostPath` flip in `values.yaml`; PVC + PV delete + `helm upgrade` re-create (see §5.2); verify backend / job pod can read union view |
| **P3: Verify** | Synthetic `DatasetConfig` upload + spot-check; cross-bank eval job; tail backend logs for any sample-missing errors                           |
| **P4: Docs**   | `architecture.md` §6 update; `operations.md` NFS section; new `runbooks/add-nfs-dataset.md`                                                   |

P2 cannot run before P1 — without a populated union mount at
`/mnt/lolday-samples`, pods that mount the recreated PVC would see an
empty directory and `spot_check_samples` would fail at submission time.
P3 cannot run before P2 — backend / job pods must see the union view
before any synthetic upload can be tested end-to-end.

## 10. Future work

- **Additional NFS sample banks** — onboarding flow documented in
  `docs/runbooks/add-nfs-dataset.md`; expected near-term as new NICT
  collections arrive
- **Retire `/mnt/ssd500g/data/samples`** — currently empty placeholder; can
  be removed once cutover is confirmed (Phase P3 complete)
- **NFS health monitoring** — Prometheus blackbox exporter probing
  `/mnt/server14/dataset` mount point; would catch silent NFS server
  outages before they manifest as job failures. Out of scope here; tracked
  separately if operator chooses to extend monitoring
- **Centralised identity (FreeIPA)** — would obviate manual cross-server
  GID alignment for future NFS sources; separate lab-level proposal

## 11. References

- mergerfs upstream: <https://github.com/trapexit/mergerfs>
- lolday samples PV chart: `charts/lolday/templates/samples-pv.yaml`
- Sample path contract: `backend/app/services/dataset.py:9`,
  `backend/app/services/dataset.py:_sample_path`
- DatasetConfig model: `backend/app/models/dataset.py`
- Existing storage layer overview: `docs/architecture.md` §6
- Storage architecture spec (MinIO + S3 backend, 2026-05-11):
  `docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md`
  — orthogonal to this spec (MinIO is for MLflow artifacts / Harbor blobs /
  Loki chunks, not detector samples)
