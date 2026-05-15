# K3s API audit log + `--secrets-encryption` (operator runbook)

> **Scope:** Enable the kube-apiserver audit log AND data-at-rest encryption
> for Secrets on the existing server30 K3s install.
>
> **Closes:** issue #167 — CIS 5.4.1 (`--secrets-encryption`) + CIS 5.5
> (`audit-log-path` / `audit-policy-file`).
>
> **SSH safety hard rule applies** — see root `CLAUDE.md` §SSH safety on
> server30 and `docs/postmortems/2026-03-31-cilium-ssh-incident.md`.

## Why

Two CIS Benchmark control failures surfaced in the 2026-05-15 post-program
review:

1. **CIS 5.5 (audit log enabled).** `scripts/setup-k3s.sh` does NOT pass
   `--kube-apiserver-arg=audit-log-path=...` /
   `--kube-apiserver-arg=audit-policy-file=...`. Without those flags, the
   kube-apiserver writes **zero** audit events. The application-level
   `audit_log` table (P5) captures lolday-domain events (job submissions,
   role changes); it does NOT capture cluster-control-plane events like
   "who issued the `kubectl create clusterrolebinding` that escalated
   service-account `lolday/backend`?" Those events go through the
   kube-apiserver and are silently dropped today.
2. **CIS 5.4.1 (Secrets encrypted at rest).** `setup-k3s.sh` does NOT pass
   `--secrets-encryption`. K3s stores Secrets in its embedded SQLite at
   `/var/lib/rancher/k3s/server/db/state.db`. Any node-root read leaks
   every Secret in plaintext — Fernet keys (`backend-fernet-secret`), MinIO
   root creds, Harbor admin password, Cloudflare tunnel credentials. Each
   of those leak-equivalents is a P0.

Per CLAUDE.md root-cause-first: this is a **structural fix to setup-k3s.sh
plus an apply-to-existing-cluster path**, not a workaround.

## What changes

- New audit policy file: `charts/lolday/files/k3s-audit-policy.yaml`. Logs
  Secret / ConfigMap / RBAC writes at `RequestResponse`, reads at
  `Metadata`, drops controller-loop noise to `None`, catches everything
  else at `Metadata`. Source: kubernetes.io auditing docs baseline example,
  tightened for lolday's credential surfaces.
- New systemd drop-in `20-lolday-audit-and-secrets.conf` under
  `/etc/systemd/system/k3s.service.d/`. Adds five
  `--kube-apiserver-arg=...` flags + `--secrets-encryption`. Log rotation
  defaults follow CIS 1.2.22 (100 MiB max, 10 backups, 30 day age).
- New script: `scripts/patch-k3s-audit-and-secrets-encryption.sh`.
  Interactive, per-step `read -r -p` confirmation. Does NOT auto-run.
- `scripts/setup-k3s.sh` is updated in a follow-up: fresh installs ship
  with these flags from the first systemd-managed boot. **This runbook is
  only for the existing server30 cluster.**

## Pre-flight checklist

In order. Do not skip.

- [ ] A **second** independent SSH session to server30 is already open in a
      separate terminal. Confirmed by `ssh -p 9453 ... 'uptime'` returning
      cleanly. **If this is the only session and the script breaks the API,
      the operator is locked out with no IPMI fallback.**
- [ ] K3s config backup:

      ```bash
      sudo cp -a /etc/rancher/k3s /etc/rancher/k3s.bak-$(date +%s)
      ```

- [ ] Datastore snapshot (rules out a botched `--secrets-encryption`
      rollout corrupting Secrets):

      ```bash
      sudo cp /var/lib/rancher/k3s/server/db/state.db \
              /var/lib/rancher/k3s/server/db/state.db.bak-$(date +%s)
      ```

- [ ] Current K3s version recorded:

      ```bash
      k3s --version | head -1 | tee ~/k3s-version-before-audit.txt
      ```

- [ ] No long-running migration is in flight (`kubectl -n lolday get jobs`
      shows nothing in `Running`). The ~30 s API restart will pause
      controllers but not drop pods; migration Jobs that hold leases for
      Postgres etc. may need a re-run, so prefer a quiet window.

## Procedure

```bash
# T-0 = maintenance window start.

# 1. Dry-run preview (no changes).
sudo bash scripts/patch-k3s-audit-and-secrets-encryption.sh
#    Confirms the audit policy path exists, prints the drop-in body,
#    explains expected effect. Read the output before --apply.

# 2. Apply interactively. Each destructive step gates on `yes` confirmation.
sudo bash scripts/patch-k3s-audit-and-secrets-encryption.sh --apply
#    The script asks once at the top ("have all three prerequisites been
#    done?") then proceeds step by step, asking again before `systemctl
#    restart k3s`.

# 3. Verify the kube-apiserver came up with the audit flags.
sudo journalctl -u k3s --since '5 min ago' \
  | grep -E 'audit-log-path|secrets-encryption' \
  | head
#    Expected: lines including `--audit-log-path=/var/log/k3s/audit.log`
#    and `--encryption-provider-config=...` (the latter is K3s-injected
#    when --secrets-encryption is set).

# 4. Verify the audit log is being written.
sudo tail -1 /var/log/k3s/audit.log | jq '{verb, resource: .objectRef.resource}'
#    Expected: one JSON line with a verb (get/list/watch/...) and a
#    resource. The file should grow under `kubectl` activity.

# 5. Verify SSH is still alive FROM A FRESH SESSION (third terminal).
ssh -p 9453 <operator>@<server30> 'uptime'
#    Hard rule: re-verify SSH after every infra step.

# 6. Re-encrypt every existing Secret under the new provider.
kubectl get secrets -A -o json | kubectl replace -f -
#    Until run, existing Secrets remain plaintext on disk even though
#    --secrets-encryption is now active. The flag affects NEW writes;
#    this one-shot replace forces every existing Secret through the
#    encryption provider. Idempotent — safe to re-run.

# 7. Spot-check a re-encrypted Secret.
SECRET=backend-fernet-key
NS=lolday
sudo strings /var/lib/rancher/k3s/server/db/state.db \
  | grep -F "${SECRET}" | head -3
#    Expected: hits show the key name but NOT the cleartext Fernet value.
#    Pre-step-6 it would have showed the cleartext base64 Fernet key
#    inline; post-step-6 the value field reads as base64-of-ciphertext.

# 8. Drop the snapshot from the pre-flight only AFTER step 7 passes.
sudo rm /etc/rancher/k3s.bak-<ts>          # tar the dir if you want to keep it
sudo rm /var/lib/rancher/k3s/server/db/state.db.bak-<ts>
```

## Verification checklist (after the window closes)

- [ ] `kubectl get nodes` shows `Ready`.
- [ ] `kubectl -n lolday get pods` is green (no CrashLoopBackOff caused by
      transient API unavailability).
- [ ] `sudo wc -l /var/log/k3s/audit.log` grows over time.
- [ ] `sudo strings /var/lib/rancher/k3s/server/db/state.db | grep <known-Fernet-prefix>` returns nothing.
- [ ] No alerts in Captain Hook (the `kube-apiserver` startup audit log
      message can briefly look anomalous to Watchdog — confirm the
      `deadmans-switch` heartbeat is still posting to Spidey Heartbeat).

## Rollback

If anything goes wrong in step 1–5:

```bash
sudo bash scripts/patch-k3s-audit-and-secrets-encryption.sh --revert
```

This removes the drop-in and restarts K3s back to the un-patched flags.
**It does NOT roll back encrypted Secrets** — Secrets re-encrypted in
step 6 stay ciphertext in the datastore. K3s still reads them transparently
because the bundled `encryption-provider-config` only adds AES-CBC on top
of the existing identity provider. If you must restore plaintext (e.g.
to migrate to a different KMS in a future runbook), restore the
pre-flight `state.db` snapshot AND the `/etc/rancher/k3s` snapshot, then
restart K3s.

If step 6 (re-encrypt) hits a Secret it cannot replace (e.g. a
ServiceAccount token Secret managed by the controller — rare in K3s
1.27+ where they are projected, not generated), the per-Secret error
surfaces from `kubectl replace`. Skip that one, file an issue, the rest
remain processed.

## Audit-trail

- Issue: #167
- Source files:
  - Audit policy → `charts/lolday/files/k3s-audit-policy.yaml`
  - Apply script → `scripts/patch-k3s-audit-and-secrets-encryption.sh`
- Upstream references:
  - kubernetes.io Auditing docs example audit policy
  - K3s docs §Customizing K3s server (drop-in `[Service] ExecStart=` pattern)
  - CIS Kubernetes Benchmark 5.4.1, 5.5, 1.2.22

## Future work

- **`setup-k3s.sh` parity.** Fresh installs should bake these flags in
  from the first boot. Follow-up PR.
- **External KMS.** K3s' bundled `--secrets-encryption` is AES-CBC under
  a server-local key derived from `/var/lib/rancher/k3s/server/cred`.
  Equivalent to disk-level encryption. A KMS-backed provider (HashiCorp
  Vault Transit, AWS KMS) would isolate the key from the node — out of
  scope for a single-node lab cluster, captured for the multi-node
  upgrade plan in `docs/architecture.md` §10.
- **Audit-log shipping.** Today the log lives on the node only. Loki
  ingestion via a Promtail sidecar or `vector` static config would let
  the audit trail outlive a node rebuild. Out of scope here; tracked as
  follow-up.
