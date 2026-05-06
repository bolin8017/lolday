# Deploy runbook (server30, K3s, Helm)

> Live runbook — derived from `docs/phase-history/phase3-deploy-runbook.md` and `docs/phase-history/host-prep.md`. Phase-specific details from those originals stay there for traceability; this runbook captures the steady-state flow.
>
> Pre-flight readers: this is the operator's runbook for bringing up the platform from a clean (or partial) state. Every step is idempotent or documents its rollback. SSH safety hard rule applies — see root `CLAUDE.md`.

## 1. Pre-requisites

### Host

- Ubuntu 24.04 on server30. NVIDIA driver installed (`nvidia-smi` works on host).
- Operator account on the host. Operator normally has **no sudo**; sudo is granted temporarily for steps marked `requires sudo`.

### One-time host sysctls

Two sysctls are required and not managed by Helm. Without them, specific pods crash at runtime:

```bash
sudo tee /etc/sysctl.d/99-lolday.conf <<'EOF'
# Required by Grafana Alloy DaemonSet (per-pod log directory watching)
fs.inotify.max_user_instances = 8192

# Required by rootless BuildKit in the build pipeline (newuidmap UID/GID map)
kernel.apparmor_restrict_unprivileged_userns = 0
EOF
sudo sysctl --system

# Verify
sysctl fs.inotify.max_user_instances              # 8192
sysctl kernel.apparmor_restrict_unprivileged_userns  # 0
```

### CLI tools

```bash
bash scripts/install-tools.sh
```

Installs kubectl, helm, k9s, etc. into `~/.local/bin/`. No sudo.

### Operator-local secret files

Copy from the committed example and fill in values out-of-band (password manager / sealed channel):

```bash
cp .lolday-secrets.env.example .lolday-secrets.env
chmod 600 .lolday-secrets.env
# fill: GRAFANA_ADMIN_PASSWORD, PG_EXPORTER_PASSWORD, CF_ENABLED,
# CF_TUNNEL_TOKEN, DISCORD_WEBHOOK_URL_EVENTS, HARBOR_ADMIN_PASSWORD,
# FERNET_KEY, plus other operator-managed values.
# CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET (machine-principal
# service token) are also in this file but only sourced manually
# for /users/me svctoken debug — see
# docs/phase-history/phase12.1-role-enum-bug.md.
```

`FERNET_KEY` generation:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If `FERNET_KEY` is regenerated after the platform is in use, every encrypted DB column becomes unreadable. Treat it as permanent.

### Confirm SSH baseline

Open a second SSH session to server30 on port 9453 _now_. Keep it open through every infra step in this runbook. If the primary session drops mid-step, use this one to recover. See `docs/postmortems/2026-03-31-cilium-ssh-incident.md` for why.

## 2. K3s install (requires sudo)

```bash
sudo bash scripts/setup-k3s.sh
```

Verify after install:

```bash
kubectl get nodes                    # server30 Ready
sudo systemctl is-active k3s         # active
```

## 3. GPU operator (no sudo)

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
helm install gpu-operator nvidia/gpu-operator \
  -n gpu-operator --create-namespace \
  --set driver.enabled=false \
  --set toolkit.enabled=true \
  --set devicePlugin.enabled=true \
  --set dcgmExporter.enabled=true \
  --wait --timeout 5m
```

Verify:

```bash
kubectl get pods -n gpu-operator
kubectl get nodes -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}'
# expect a non-zero number
```

## 4. Cloudflare Access App + tunnel setup

Out-of-band steps in the Cloudflare dashboard:

1. **Tunnel** — create or reuse a tunnel for server30. Copy the tunnel token. Save to `.lolday-secrets.env` as `CF_TUNNEL_TOKEN`.
2. **Access App** — create a self-hosted application for the lolday domain. Configure desired identity provider and policies. Note:
   - Team domain (e.g. `<your-team>.cloudflareaccess.com`) → set in Helm `values.yaml` overrides as `backend.cfAccess.teamDomain` (or env `CF_ACCESS_TEAM_DOMAIN`).
   - App AUD claim (64-char hex) → `CF_ACCESS_APP_AUD`.
3. **Service token (optional)** — create a service token for machine principals. Save id/secret to `.lolday-secrets.env` as `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`.

Backend boot in production rejects empty `CF_ACCESS_TEAM_DOMAIN` or `CF_ACCESS_APP_AUD`.

## 5. Deploy the platform (no sudo)

The deploy script reads `charts/lolday/helpers.lock` and injects the two helper image refs via Helm `--set`. **The lock must exist and match the current HEAD** — see `docs/runbooks/release-helpers.md` for the bootstrap order on a fresh install. A missing or drifted lock exits 1 with a remediation message.

```bash
bash scripts/deploy.sh
```

Internally: `helm dependency update charts/lolday` then `helm upgrade --install lolday charts/lolday -n lolday`. The script reads `.lolday-secrets.env` for operator-managed secrets and `charts/lolday/helpers.lock` for helper image refs.

Watch:

```bash
kubectl get pods -n lolday -w
```

Initial cold start: ~5–10 minutes. Harbor often comes up last.

## 6. Alembic upgrade hook (automatic)

`charts/lolday/templates/alembic-upgrade-hook.yaml` is a Helm `pre-upgrade` Job that runs `alembic upgrade head` against the live DB before the new backend pod starts. Backend boot fails fast (RuntimeError) if the hook didn't reach `head`.

Verify the hook ran:

```bash
kubectl get jobs -n lolday | grep alembic-upgrade
kubectl logs -n lolday job/$(kubectl get jobs -n lolday -o name | grep alembic-upgrade | head -1 | sed 's|job/||')
# expect: "INFO  [alembic.runtime.migration] Running upgrade ... -> <head>"
```

If the backend pod is CrashLoopBackOff with `DB schema mismatch`: the hook didn't run or rolled back. Re-run `helm upgrade --install lolday charts/lolday -n lolday` to recreate the Job.

## 7. Verification checklist

- `kubectl get pods -n lolday` — all Running
- `kubectl get vc -n lolday` — Volcano queue exists (`vcjob` CRD installed)
- `kubectl get servicemonitor -n monitoring` — six entries: backend, dcgm, postgres, traefik, trivy, volcano
- Health endpoint: `curl -k https://<lolday-domain>/healthz` (frontend) and `/api/v1/health` (backend, behind CF Access)
- Grafana reachable; default dashboards present (`dcgm`, `postgresql`, `reconciler-errors`, `traefik`, `trivy-security`)
- Trigger a small detector build → confirm Discord events webhook fires
- Wait one full deadmans-switch CronJob period (default 5 min) → confirm a heartbeat lands in the deadmans-switch Discord channel

## 8. Rollback

### Helm-level (recommended)

```bash
helm history lolday -n lolday
helm rollback lolday <prev-rev> -n lolday
```

### Schema rollback

**Never run `alembic downgrade` in production.** Write a forward migration that reverses the schema change and ship it via the normal upgrade hook.

### Sub-chart rollback

If a sub-chart bump regressed (e.g. kube-prometheus-stack), pin the previous version in `charts/lolday/Chart.yaml` and re-run `helm dependency update charts/lolday` then `bash scripts/deploy.sh`.

### Full nuclear reset (data loss)

```bash
helm uninstall lolday -n lolday
kubectl delete pvc -n lolday --all
kubectl delete namespace lolday
```

Then re-deploy from step 5. This wipes all DB state, MLflow runs, and Harbor images. Use only for fresh installs / dev environments.

## 9. Maintenance

### Upgrading kubelet args on an existing K3s

When `scripts/setup-k3s.sh` evolves to add new `--kubelet-arg=...` (e.g. Phase 0 of the GPU scheduling & OOM defense design — `kube-reserved`, `system-reserved`, memory eviction), the existing server30 install does **not** auto-pick them up. Use the patch script to upgrade in place.

```bash
# 1. dry-run — prints the systemd drop-in that would be written, no changes
sudo bash scripts/patch-k3s-kubelet-args.sh

# 2. SSH safety verify (root CLAUDE.md hard rule)
#    Operator opens a SECOND ssh session to server30 (port 9453) from a different
#    terminal and leaves it idle as a canary. That session must stay alive
#    across the restart.

# 3. apply — writes drop-in, daemon-reload, restart k3s, waits for Ready
sudo bash scripts/patch-k3s-kubelet-args.sh --apply

# 4. verify
bash tests/2026-05-05-kubelet-args-smoke.sh

# 5. (rollback path) — same script
sudo bash scripts/patch-k3s-kubelet-args.sh --revert
```

Expected effects of `--apply`:

- ~30 s K3s server restart. SSH unaffected (control plane only).
- Pod runtime (containerd) does **not** restart; in-flight detector pods keep running.
- New `Allocatable.memory` ≈ Capacity − 7 GiB (= kube 2 + system 4 + eviction-hard 1).

Spec: `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` §7 (Phase 0).
Plan: `docs/superpowers/plans/2026-05-05-gpu-scheduling-phase0-kubelet-args.md`.

### Migrating vcjobs to the lolday-jobs namespace (one-time, Phase 1)

**Pre-flight (run before `bash scripts/deploy.sh`):**

```bash
bash scripts/migrate-jobs-namespace.sh check
```

If any in-flight vcjob is reported, wait for it to finish (or cancel via UI) before deploying. New vcjobs will land in `lolday-jobs`; old in-flight ones in `lolday` continue to run but won't be visible to the new reconciler. Build jobs are similar — in-flight builds finish in `lolday`, new ones go to `lolday-jobs`.

**Deploy:**

```bash
bash scripts/deploy.sh
```

Helm creates the new namespace + quotas + limits + RBAC + NetworkPolicies on first install. Backend `Deployment` rolls with the new `JOB_NAMESPACE / BUILD_NAMESPACE = lolday-jobs` env.

**Post-verify:**

```bash
bash scripts/migrate-jobs-namespace.sh post-verify
bash tests/2026-05-05-jobs-namespace-smoke.sh
```

Submit a small detector evaluate from the UI; confirm the new vcjob lands in `lolday-jobs` (`kubectl get vcjobs -n lolday-jobs`).

**Rollback:**

```bash
helm rollback lolday <prev-rev> -n lolday
```

The `lolday-jobs` namespace is left in place (cheap to keep empty). Any vcjob already created in `lolday-jobs` decays via TTL. Only the `JOB_NAMESPACE` env reverts; new submissions go back to `lolday`.

Spec: `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` §7 (Phase 1).
Plan: `docs/superpowers/plans/2026-05-05-gpu-scheduling-phase1-jobs-namespace.md`.

## 10. Release flow (backend + frontend image bump)

Sister doc to `docs/runbooks/release-helpers.md` (helper-image releases).

Release commits **must** bump four fields together:

- `charts/lolday/Chart.yaml` — `version` and `appVersion` (must match each other)
- `charts/lolday/values.yaml` — `backend.image` tag and `frontend.image` tag (both must be `vX.Y.Z`)

Pre-commit hook `image-tags-aligned` (`scripts/check-image-tags-aligned.sh`) blocks any commit that leaves these out of sync. Added 2026-05-06 after v0.18.0 shipped with a stale frontend tag — see PR #96 history.

Standard sequence from a clean working tree on `main`:

```bash
git checkout -b chore/release-vX.Y.Z

# Build + push both images. Operator host has /etc/hosts pointing
# harbor.lolday.svc.cluster.local at the in-cluster Harbor.
( cd backend  && docker build -t harbor.lolday.svc.cluster.local:80/lolday/lolday-backend:vX.Y.Z . )
( cd frontend && docker build -t harbor.lolday.svc.cluster.local:80/lolday/lolday-frontend:vX.Y.Z . )
docker push harbor.lolday.svc.cluster.local:80/lolday/lolday-backend:vX.Y.Z
docker push harbor.lolday.svc.cluster.local:80/lolday/lolday-frontend:vX.Y.Z

# Bump the four fields above; pre-commit blocks any half-bump.
git commit -am "chore(release): cut vX.Y.Z — <one-line summary>"
git push -u origin HEAD && gh pr create

# After PR merge
bash scripts/deploy.sh
```
