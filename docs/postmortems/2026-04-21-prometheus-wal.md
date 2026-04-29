# Post-mortem: Prometheus WAL corruption — 2026-04-21

**Date:** 2026-04-21 (started), noticed 2026-04-22 during phase 9 review
**Duration:** ~18 hours silent
**Severity:** S2 — no user-visible platform impact, but monitoring was dark
**Detected by:** operator reviewing Discord alert history, not automation

## What happened

At **2026-04-21 13:11 UTC** (21:11 TST), Prometheus started failing every
scrape commit and every rule evaluation with:

```
level=ERROR msg="Scrape commit failed" ... err="write to WAL: log samples: create new segment file: open /prometheus/wal/00000030: no such file or directory"
```

Rule evaluation health went to `err` and stayed there. Because rule
evaluation is what produces `up{...}` fixtures for `KubeAPIDown` /
`KubeletDown` alerts (both use `absent(up{...})`), those alerts fired
and showed up in `#lolday-alerts-critical`. The _actual_ cluster was
fine — apiserver and kubelet were healthy throughout.

Prometheus continued running for another ~18 hours in this broken state,
and no other failure surfaced because Prometheus cannot alert on its own
death: when the WAL is write-broken, recording rules cannot persist,
`PrometheusTSDBCompactionsFailing` / `PrometheusTSDBReloadsFailing` can't
fire, and `Watchdog` stays as a stale startsAt that Alertmanager still
re-delivers but no external consumer was watching freshness.

## Root cause

The Prometheus PVC `prometheus-kps-prometheus-db-prometheus-kps-prometheus-0`
uses `subPath: prometheus-db` on a local-path hostPath mount. The
subdirectory `/var/lib/rancher/k3s/storage/<pvc>/prometheus-db/` was
deleted on the host filesystem while Prometheus was still running. The
bind mount into the container stayed alive (kernel holds the inode),
so writes went to `(deleted)`:

```
# /proc/1/mountinfo inside the pod, 2026-04-22:
... /prometheus-db//deleted /prometheus rw,relatime - ext4 ...
```

Exact trigger uncertain — possible candidates:

- A failed helm upgrade (rev 49, `2026-04-21 11:57 UTC`) that partially
  reconciled StatefulSets
- local-path-provisioner cleanup race
- Manual `rm -rf` (no evidence in `~/.bash_history`)

## Impact

- 18 h of metric gap (Prometheus scraped but could not persist)
- Two false-positive `KubeAPIDown` / `KubeletDown` pages
- One missed `PrometheusTSDBCompactionsFailing` opportunity (the very
  alert that would have caught this class of issue)
- No service impact — backend, Harbor, MLflow, builds, training all
  continued working throughout

## Fix

`kubectl delete pod prometheus-kps-prometheus-0 --grace-period=0 --force`
— StatefulSet controller recreated the pod; kubelet remounted the PVC
and recreated the deleted subPath directory. Prometheus came up clean
with an empty TSDB. Alerts cleared within 2 minutes.

## Preventive work (Phase 9)

Phase 9 ships three independent layers:

1. **Dead Man's Switch** (`charts/lolday/templates/monitoring/deadmans-switch.yaml`
   - `charts/lolday/files/deadmans_switch/check.py`): a CronJob that
     every 5 minutes asks Alertmanager for a fresh `Watchdog.updatedAt`.
     If the alert is missing or stale, it POSTs directly to the Discord
     critical webhook, bypassing Prometheus and Alertmanager entirely.
     This would have caught this incident within 15 minutes.

2. **Storage consolidation onto `/mnt/ssd500g/`**: samples PV moved off
   the root LV; new PVCs default to the NVMe. Reduces the chance of
   an accidental cleanup script targeting `/var/lib/rancher/k3s/storage`
   wiping a live Prometheus mount.

3. **Unit-tested DMS check logic** (`backend/tests/test_deadmans_switch_check.py`):
   13 tests covering every parse / retry / failure-mode branch so a
   future refactor to the heartbeat script can't silently break the
   only independent observability path.

## Action items (tracked separately)

- [ ] Migrate remaining root-LV PVCs (Harbor registry, MLflow, Loki,
      Prometheus, Postgres, Grafana) onto `/mnt/ssd500g/k3s-storage/`
      so an accidental root-lv rm can't touch them either.
- [ ] Consider `Retain` reclaim on the Prometheus PV (currently Retain
      already; confirm on fresh clusters).
- [ ] Investigate whether the helm pre-upgrade hook chain in rev 49
      could have caused the subPath delete — not reproduced in tests.
