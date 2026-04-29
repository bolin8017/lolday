# Troubleshooting (symptom → action)

> Symptom-keyed lookup for fast triage. Where a script exists for the symptom, point at it. Where a phase-specific incident covers it, link the relevant `docs/phase-history/` or `docs/postmortems/` file.

### Symptom: backend 401 on every request / can't log in

**Cause hypothesis:** Cloudflare Access JWT not reaching backend, or `CF_ACCESS_TEAM_DOMAIN` / `CF_ACCESS_APP_AUD` misconfigured, or the JWKS cache is poisoned.

**Action:**

```bash
bash scripts/diag-backend-401.sh
```

Confirm the CF tunnel is up (`kubectl get pods -n lolday | grep cloudflared`). Verify backend env has both `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_APP_AUD` set (a single empty value silently fails JWT verify and produces 401 for everything).

### Symptom: backend pod CrashLoopBackOff with "DB schema mismatch"

**Cause hypothesis:** Alembic upgrade hook didn't run or rolled back. `_assert_schema_at_head()` is intentionally fail-fast; the new backend code expects a column the live DB doesn't have.

**Action:**

```bash
kubectl get jobs -n lolday | grep alembic-upgrade
kubectl logs -n lolday job/<alembic-upgrade-...>
```

Re-run `helm upgrade --install lolday charts/lolday -n lolday` to recreate the Job. If it fails, inspect the migration that errored and fix forward.

### Symptom: backend pod CrashLoopBackOff with "AUTH_DEV_MODE=true is forbidden when ENVIRONMENT=production"

**Cause hypothesis:** Someone overrode env to bypass Cloudflare Access for local dev and forgot to revert. Production rejects this at boot — by design.

**Action:** Set `ENVIRONMENT=development` for local dev sessions, or remove the `AUTH_DEV_MODE` override from values.yaml / Helm `--set` flags. Never set both `AUTH_DEV_MODE=true` and `ENVIRONMENT=production`.

### Symptom: K3s pulls from Harbor with 401 / containerd errors

**Cause hypothesis:** K3s containerd registry config is missing the Harbor mirror, OR the lolday Harbor project's robot creds are out of sync.

**Action:**

```bash
sudo bash scripts/patch-k3s-registries.sh        # idempotent; review diff
bash scripts/fix-lolday-project-public.sh        # ensures project is public if needed
bash scripts/recover-harbor.sh                   # full Harbor robot creds reset (last resort)
```

Run `harbor-inventory.sh` to confirm what's actually in Harbor before assuming an image is missing.

### Symptom: PV data appears missing

**Cause hypothesis:** PVC was rebound without the data being copied; or Stage-4 / Phase 9.6-style hostPath drift.

**Action:**

```bash
bash scripts/diag-pv-data.sh
bash scripts/find-lost-data.sh
```

Phase 9.6 incident notes are in `docs/phase-history/phase11d-retirement-findings.md` and the `migrate-*.sh` scripts.

### Symptom: disk full / `/` full

**Cause hypothesis:** PVC ephemeral storage on root LV; logs filling /var; image cache.

**Action:**

```bash
bash scripts/disk-diag.sh         # locate biggest consumers (root only)
```

Phase 8.2 / 9.6 migrations move PVCs off root LV. See `scripts/migrate-ephemeral-to-ssd.sh` and `scripts/migrate-all-root-pvcs.sh` for the migration playbooks (require sudo + careful review).

### Symptom: Volcano scheduling stalled / `lolday_volcano_pending_stale` alert

**Cause hypothesis:** Volcano controller pod crashed, GPU device-plugin lost the node, or queue mis-configured.

**Action:**

```bash
kubectl get vc -n lolday
kubectl describe vcjob -n lolday <name>
kubectl get pods -n volcano-system
kubectl logs -n volcano-system <volcano-controller-pod>
```

The alert fires on Pending jobs older than `VOLCANO_STALE_SECONDS` (default 1800s). It's a Gauge alert, not a Counter — it can drop back to 0.

### Symptom: Discord notifications missing

**Cause hypothesis:** Webhook URL secret empty / wrong, or delivery is failing silently (fire-and-forget swallows exceptions).

**Action:** Check the Prom counter:

```promql
rate(lolday_backend_errors_total{stage="discord_notify"}[5m])
```

Then verify the secret:

```bash
kubectl get secret -n lolday discord-events -o yaml
kubectl get deployment -n lolday backend -o yaml | grep -A1 DISCORD_WEBHOOK_URL_EVENTS
```

If `DISCORD_WEBHOOK_URL_EVENTS` is empty, backend logs a startup warning but does not refuse to boot — that's intentional. Notifications return early in `notify.post_webhook` when the URL is empty.

Service-token-driven jobs do not notify — by design (Phase 12). Don't try to "fix" this.

### Symptom: CSP blocks loaded script in the SPA

**Cause hypothesis:** The production frontend nginx CSP is `script-src 'self'`. Any inline `<script>` is blocked at runtime.

**Action:** Move inline scripts to bundled JS files. Test against the built container image, not just `pnpm dev`. This is not a bug — relaxing CSP is not the right fix.

### Symptom: about to make a Cilium / iptables / sysctl / UFW change

**Cause hypothesis:** A change at this layer might drop the SSH connection on port 9453, with no out-of-band recovery available.

**Action:** STOP. Read root `CLAUDE.md` SSH safety hard rule and `docs/postmortems/2026-03-31-cilium-ssh-incident.md`. Dry-run the change to stdout, prompt the operator to verify SSH from a fresh session, and apply only after explicit confirmation.

### Symptom: deadmans-switch CronJob CrashLoopBackOff with "DISCORD_URL env var missing"

**Cause hypothesis:** The CronJob's env is missing `DISCORD_URL`. This is intentionally fail-fast — a silent dead-man switch is worse than a crashing one.

**Action:** Set `DISCORD_URL` in the deadmans-switch CronJob env via Helm values. The webhook is **independent** of `DISCORD_WEBHOOK_URL_EVENTS` (different channel).

### Symptom: `helm dependency update` complains about missing repos

**Cause hypothesis:** Helm repos aren't added locally.

**Action:**

```bash
helm repo add harbor https://helm.goharbor.io
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add aqua https://aquasecurity.github.io/helm-charts
helm repo add volcano-sh https://volcano-sh.github.io/helm-charts
helm repo update
```

Then re-run `helm dependency update charts/lolday`.
