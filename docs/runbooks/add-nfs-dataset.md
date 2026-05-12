# Adding a new NFS-backed sample bank

Operational checklist for onboarding a new dataset (NFS sample bank) into the
lolday `samples_root` view, without copying data and without backend / chart
redeploy.

**Pre-requisites:**

- The new dataset is already exported from some NFS server reachable from
  server30
- The internal directory structure of the dataset is lolday's hex-prefix
  convention: `<source>/<sha256[:2]>/<sha256>` flat files
- You have sudo on server30
- You know the priority of the new bank relative to existing banks
  (leftmost in the mergerfs branch list = highest priority on SHA-256
  collision)

## Procedure

### 1. Verify the new NFS source is reachable

```bash
ping -c 2 -W 2 <nfs-server-ip>
```

### 2. Add the NFS mount on server30

If the NFS source is on a server already used (e.g. `140.118.155.14`),
mount a new sub-directory the same way as the existing one. If it is a new
server, follow `docs/runbooks/deploy.md` SSH-safety discipline first.

Example fstab line (read-only, soft-mount, boot-safe):

```
<nfs-server-ip>:/path/on/server  /mnt/<short-name>  nfs  ro,_netdev,nofail,soft,timeo=30,retrans=2  0  0
```

Activate:

```bash
sudo mkdir -p /mnt/<short-name>
sudo systemctl daemon-reload
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
priority position you want — leftmost = highest priority.

Example: to add a hypothetical `nict202605/nictMalware` with the highest
priority:

```diff
- /mnt/server14/dataset/nict202503/nictMalware:/mnt/server14/dataset/nict202403/nictMalware:/mnt/server14/dataset/benignware/data  /mnt/lolday-samples  ...
+ /mnt/server14/dataset/nict202605/nictMalware:/mnt/server14/dataset/nict202503/nictMalware:/mnt/server14/dataset/nict202403/nictMalware:/mnt/server14/dataset/benignware/data  /mnt/lolday-samples  ...
```

**Do not include `nofail` in the mergerfs options.** mergerfs v2.33.5
forwards `nofail` to the FUSE driver which rejects it. Boot safety still
holds because the NFS source mount lines have `nofail` and `_netdev`.

### 4. Remount the union

```bash
sudo umount /mnt/lolday-samples
sudo systemctl daemon-reload
sudo mount -a
mountpoint /mnt/lolday-samples
ls /mnt/lolday-samples/ | wc -l   # expect 256
```

The `umount` of mergerfs should succeed instantly because the union is
read-only — no dirty pages to flush. If any detector job is mid-read, it
will see a brief I/O error; consider running this during a quiet window.

### 5. Verify the new bank surfaces under the union

Pick a SHA-256 known to live only in the new bank:

```bash
NEW_SHA=$(ls /mnt/<short-name>/<source-subdir>/00/ | head -1)
echo "Picked: $NEW_SHA"
stat /mnt/lolday-samples/00/$NEW_SHA   # should succeed
```

Verify dedup priority via mergerfs xattr (replace `<prefix>` and `<sha>`
with a SHA-256 known to exist in **multiple** banks):

```bash
python3 -c "import os; print(os.getxattr('/mnt/lolday-samples/<prefix>/<sha>', 'user.mergerfs.fullpath').decode())"
# Should print the source path of the highest-priority branch that holds
# this file.
```

### 6. Upload the bank's CSV as a lolday `DatasetConfig`

Use the lolday UI (`Datasets → New`) and upload the CSV that ships with
the bank (e.g. `nictMalware_info.csv`). Spot-check will pass because every
CSV row's SHA-256 resolves under the union.

For cross-bank training datasets, build a deduped combined CSV in pandas
(see spec §5.3) and upload that.

## Removing a bank

The reverse: remove the path from the mergerfs fstab line, remount.

Any `DatasetConfig` row whose CSV references samples in the removed bank
will fail `spot_check_samples` on next job submission. This is the
intended behaviour — loud failure beats silent missing-sample training
data.

## Troubleshooting

| Symptom                                                                      | Likely cause                                      | Action                                                                                                                    |
| ---------------------------------------------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `mount.fuse.mergerfs: command not found` after fstab edit                    | mergerfs uninstalled                              | `sudo apt-get install fuse mergerfs`                                                                                      |
| `fuse: unknown option 'nofail'` on `mount -a`                                | `nofail` in mergerfs options (v2.33.5 bug)        | Remove `nofail` from the mergerfs fstab options field                                                                     |
| Union mount empty after remount                                              | typo in fstab branch path                         | `findmnt --verify /etc/fstab`; restore from `/etc/fstab.bak-*`                                                            |
| Detector pod `ENOENT` on a known sample                                      | union not picking up the source's `00..ff` layout | verify the source has the right internal structure (`ls /mnt/<short-name>/<source-subdir>/`)                              |
| Cross-bank duplicate served from wrong bank                                  | branch order wrong in fstab                       | edit fstab branch order; `umount /mnt/lolday-samples && mount -a`                                                         |
| Group of files shows as raw GID number (e.g. `1002`) instead of `diskaccess` | GID misalignment between server30 and NFS source  | server-side fix via `/home/bolin8017/Documents/server30-gid-realign.sh` style operation; cosmetic, does not affect access |

## Related

- Spec: `docs/superpowers/specs/2026-05-12-nfs-dataset-union-mount-design.md`
- Architecture: `docs/architecture.md` §6.7 (Detector samples)
- Operations quick-ref: `docs/operations.md` §NFS dataset sources
