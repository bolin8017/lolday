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

### Symptom: Active detector version disappears from Harbor (vcjob: ImagePullBackOff: not found)

**Symptom** — `DetectorVersion.status = ACTIVE` in DB and `image_digest` populated; vcjob fails to pull `harbor.lolday.svc:80/detectors/<name>:<tag>` with 404; `docker pull` of the same tag also returns 404.

**Read** — Harbor no longer has the tag/digest. Likely sources:

1. Pre-v0.20.7 digest-level delete footgun — sibling tag deletion took the manifest with it.
2. Retention policy GC.
3. Manual Harbor cleanup.

**Recovery (preferred)** — re-build through the lolday API:

```bash
JWT=...   # CF Access token; copy from browser cookie / DevTools
DET_ID=...
curl -X POST "https://lolday.../api/v1/detectors/$DET_ID/builds" \
  -H "Cookie: CF_Authorization=$JWT" \
  -H "Content-Type: application/json" \
  -d '{"git_tag": "v4.1.0"}'
```

The unique constraint `(detector_id, git_tag)` blocks a duplicate ACTIVE row. If a stale ACTIVE row exists pointing at the missing image, soft-delete it first (`DELETE /api/v1/detectors/$DET_ID/versions/<tag>`) before re-building. The new build pushes the same content; BuildKit usually reproduces the original digest.

**Fallback (Harbor writable but the build pipeline is broken)** — pull from a workstation cache and re-push to Harbor:

```bash
docker pull harbor.lolday.svc:80/detectors/<name>:<tag>   # confirms the 404
# from a workstation that still has the image cached:
docker tag  <local-image> harbor.lolday.svc:80/detectors/<name>:<tag>
docker push harbor.lolday.svc:80/detectors/<name>:<tag>
```

Detector images are not in CI's GHCR registry today (CI builds backend / frontend / helpers; detector images are operator-built). The fallback applies only if a workstation kept the image in its local docker cache.

**Prevention** — v0.20.7+ uses tag-level Harbor delete; multi-tag-shared-digest scenarios no longer cascade.

## Symptom: GpuStatusBanner shows "scheduler in fail-safe mode"

**Cause:** backend cannot reach Prometheus (kps pod restarting, DNS issue, network policy).

**Diagnosis:**

1. `kubectl -n monitoring get pods -l app.kubernetes.io/name=prometheus` — confirm pod is Ready.
2. From a backend pod: `kubectl -n lolday exec deploy/backend -- curl -s http://kps-prometheus.monitoring.svc:9090/-/ready` — expect `Prometheus Server is Ready.`.
3. Check NetworkPolicy: `kubectl -n lolday describe networkpolicy backend` — egress to monitoring ns must be allowed.

**Mitigation (if Prom is genuinely down):**

- Temporary escape hatch: `kubectl -n lolday set env deploy/backend GPU_SIGNAL_FAIL_SAFE_BLOCK=false` — falls back to K8s-only counting until Prom recovers. Revert once Prom is healthy.

## Symptom: GpuStatusBanner flags external use, but no one is using GPU

**Cause:** DCGM exporter `--kubernetes` flag is missing, so all GPU activity (including lolday's own) is classified as external because the `exported_namespace` label is empty.

**Diagnosis:**

1. `kubectl -n gpu-operator get ds -l app=nvidia-dcgm-exporter -o yaml | grep -- --kubernetes` — expect at least one match.
2. If missing, check the gpu-operator ClusterPolicy: `kubectl get clusterpolicy gpu-cluster-policy -o yaml | grep kubernetes` — under `dcgmExporter`, look for `kubernetes: true` (default true).

**Mitigation:** Re-apply the gpu-operator default ClusterPolicy. See gpu-operator docs.

## Symptom: DCGMXIDError fired

**Cause:** NVIDIA driver reported a non-zero XID error code on a GPU.

**Diagnosis:**

1. Note the `gpu` and `Hostname` labels from the Discord alert.
2. SSH to the affected host and run:
   ```
   sudo dmesg | grep -i "NVRM: Xid"
   ```
3. Match the XID code to https://docs.nvidia.com/deploy/xid-errors/.
   Common codes: `13` (graphics engine exception, often app bug), `31`
   (GPU memory page fault, often app bug), `48`/`63`/`64`/`74` (uncorrectable
   ECC / row remap — hardware degradation, replace card if recurring).
4. Check `dcgmi diag -r 1` (level-1 health check) on the host.

**Mitigation:**

- App-bug-level XIDs (13, 31): may be transient — restart the offending
  pod / vcjob. If persistent, investigate the workload.
- Hardware-degradation XIDs: schedule the card for replacement.
  Cordon the node; lolday will fail-safe (no dispatch).

## Symptom: GpuSignalFailSafeStuck fired

**Cause:** Backend's host-aware GPU signal (議題 A) has been in fail-safe
mode for 30+ minutes — Prometheus is unreachable.

**Diagnosis:** Same as the existing
"GpuStatusBanner shows 'scheduler in fail-safe mode'" SOP above. This
alert is the 30-min escalation of that condition.

## Symptom: Discord critical channel suddenly noisy from a single incident

**Cause:** Inhibition rule failed to apply.

**Diagnosis:**

1. Inspect the rules:
   ```
   amtool --alertmanager.url=http://localhost:9093 \
     config show | yq eval '.inhibitRules' -
   ```
2. Confirm 5 inhibitRules are present (see spec §6.2).
3. If a rule is missing or malformed, the chart-side yaml has drifted.
   Re-render with `helm template` and compare to the chart source.

## Symptom: GpuStatusBanner shows util > 0% but vram_used_mb = 0

**Cause:** DCGM exporter is reporting `DCGM_FI_DEV_GPU_UTIL` but not
`DCGM_FI_DEV_FB_USED` (or the FB metric is consistently 0). Observed live
on 2026-05-10 post-deploy: GPUs showed `state="external"` with util 95–97%
but `vram_used_mb=0`.

**Diagnosis:**

1. Check what DCGM is exposing on the cluster:
   ```bash
   kubectl -n monitoring port-forward svc/kps-prometheus 9090:9090 &
   curl -s 'http://localhost:9090/api/v1/query?query=DCGM_FI_DEV_FB_USED' \
     | jq .data.result
   ```
2. If empty: gpu-operator's DCGM exporter ConfigMap may not include the
   `DCGM_FI_DEV_FB_USED` field. Check the metric list:
   ```bash
   kubectl -n gpu-operator get configmap -l app=nvidia-dcgm-exporter \
     -o yaml | grep -A1 DCGM_FI_DEV_FB_USED
   ```
3. If missing, the metric needs to be added to the dcgm-exporter ConfigMap
   (gpu-operator default config covers most fields, but custom overlays
   may have trimmed it).

**Mitigation:** UI-side `vram_used_mb=0` is cosmetic — the classification
logic uses an OR of util-threshold and vram-threshold, so external GPU
detection still works via util alone. Fix the metric for accurate UI
display, but it does not block the scheduler decision.
