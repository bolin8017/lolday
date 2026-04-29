# Phase 3 Deploy Runbook (Task 17)

Prerequisite: Phase 3 code merged to `dev` (commit `855ada0` or later). All 78 backend tests pass. Helm template renders clean. Both Dockerfiles build clean.

**Run on server30** from `/home/bolin8017/Documents/repositories/lolday` on branch `dev`.

This runbook is resumable — each step is idempotent or documents its rollback.

---

## 0. Pull latest + sanity check

```bash
cd /home/bolin8017/Documents/repositories/lolday
git fetch origin
git checkout dev
git pull

# Verify state
kubectl get nodes                      # K3s reachable
docker ps | head -3                     # Docker works
ls scripts/patch-k3s-registries.sh      # exists, should be +x
```

If `kubectl` fails: `sudo systemctl start k3s`.

---

## 1. Generate and save secrets

```bash
export FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export HARBOR_ADMIN_PASSWORD="$(openssl rand -base64 24)"
export PG_PASSWORD="$(openssl rand -base64 24)"
export JWT_SECRET="$(openssl rand -base64 48)"
export ADMIN_EMAIL="admin@lolday.dev"
export ADMIN_PASSWORD="$(openssl rand -base64 18)"

# SAVE THESE — especially HARBOR_ADMIN_PASSWORD (login to Harbor UI later)
echo "==== SAVE THESE SECRETS ===="
echo "HARBOR_ADMIN_PASSWORD=$HARBOR_ADMIN_PASSWORD"
echo "ADMIN_EMAIL=$ADMIN_EMAIL"
echo "ADMIN_PASSWORD=$ADMIN_PASSWORD"
echo "PG_PASSWORD=$PG_PASSWORD"
echo "FERNET_KEY=$FERNET_KEY"
echo "JWT_SECRET=$JWT_SECRET"
echo "============================"
```

Write these to a password manager or secure file OUTSIDE the repo before continuing. If you lose `HARBOR_ADMIN_PASSWORD` after step 3, reset via Harbor's DB; if you lose `FERNET_KEY` after use, every stored PAT becomes unreadable.

---

## 2. Handle Phase 2 DB coexistence (IMPORTANT)

Phase 2 created the `user` table via `Base.metadata.create_all()` on lifespan — no Alembic migration was committed. Phase 3's migration `f5c431c00187_add_detector_tables.py` has `down_revision = None` and its `upgrade()` includes the `user` table.

**If you run `alembic upgrade head` against the existing Phase 2 DB, it will fail with "relation user already exists".**

**Chosen path: clean re-deploy (no Phase 2 data preserved).** This is acceptable because lolday is pre-production; any registered users are test accounts. Execute:

```bash
# Wipe PVCs so PostgreSQL starts fresh
helm uninstall lolday -n lolday 2>/dev/null || true
kubectl delete pvc -n lolday --all 2>/dev/null || true

# Verify namespace clean
kubectl get all -n lolday
# expect: no resources found
```

Alternative if you must preserve Phase 2 data: after step 3 deploys, exec into the postgres pod and `alembic stamp f5c431c00187` manually to mark migration as applied without running it; THEN run `alembic upgrade head` to add just the `c13efbf4` build_token/pending_schema columns. Document which approach you used.

---

## 3. Deploy via Helm

```bash
bash scripts/deploy.sh
```

Expected output:
- `[1/4] Pre-flight checks...` → Cluster OK
- `[2/4] Preparing Helm dependencies...` → downloads Harbor 1.16 tarball
- `[3/4] Ensuring namespaces...` → creates `lolday` and `harbor`
- `[4/4] Deploying lolday...` → ~5-8 min for Harbor to come up
- Final `kubectl -n lolday get pods` → backend may be CrashLoopBackOff initially (can't reach Harbor yet); Harbor pods Running

**Wait for Harbor:**
```bash
kubectl wait --for=condition=Ready pod -l app=harbor,component=core -n harbor --timeout=10m
kubectl -n harbor get pods
# All Ready
```

**Verify backend state:**
```bash
kubectl -n lolday logs deployment/backend --tail=30
```

Expected in logs:
- `Seed admin created: admin@lolday.dev` OR `Seed admin already exists` (idempotent)
- `harbor init` success OR `ensure_project failed` retries
- `build reconciler started`

If backend is CrashLoopBackOff with "image pull error for localhost:5000/lolday-backend:latest" — that's Phase 2's image reference. We'll fix it in step 6 once Harbor has the new image.

---

## 4. Patch K3s containerd for Harbor access (REQUIRES SUDO)

```bash
sudo bash scripts/patch-k3s-registries.sh
```

Review the proposed diff carefully. Script behavior:
1. Reads Harbor Service ClusterIP
2. Backs up `/etc/rancher/k3s/registries.yaml` → `.bak.<timestamp>`
3. Shows `diff -u` and asks `Apply? [y/N]`
4. Answer `y` → writes file, restarts k3s
5. Verifies `systemctl is-active k3s`; if fails, auto-rollback + exit code 2

**SSH SAFETY:** port 9453 should stay up throughout. If SSH drops during this step, you have a big problem. If connection feels laggy after `systemctl restart k3s`, wait ~30s — kubelet re-registering takes time.

**Verify after:**
```bash
sudo systemctl is-active k3s    # active
kubectl get pods -A             # all namespaces healthy
cat /etc/rancher/k3s/registries.yaml
# mirrors: { harbor.harbor.svc:80: { endpoint: [http://<ClusterIP>:80] } }
```

---

## 5. Build + push images to Harbor

Harbor is `expose.type: clusterIP` (not exposed outside cluster). Use `kubectl port-forward` for push.

```bash
# Port-forward Harbor in background
kubectl port-forward -n harbor svc/harbor 8080:80 &
PF_PID=$!
sleep 3

# Login
docker login localhost:8080 -u admin -p "$HARBOR_ADMIN_PASSWORD"

# Build + push backend
docker build -t harbor.harbor.svc:80/lolday/lolday-backend:phase3 backend/
docker tag harbor.harbor.svc:80/lolday/lolday-backend:phase3 localhost:8080/lolday/lolday-backend:phase3
docker push localhost:8080/lolday/lolday-backend:phase3

# Build + push build-helper
docker build -t harbor.harbor.svc:80/lolday/build-helper:v1 charts/lolday/helpers/build-helper/
docker tag harbor.harbor.svc:80/lolday/build-helper:v1 localhost:8080/lolday/build-helper:v1
docker push localhost:8080/lolday/build-helper:v1

# Stop port-forward
kill $PF_PID
```

Verify both images in Harbor:
```bash
kubectl port-forward -n harbor svc/harbor 8080:80 &
PF_PID=$!; sleep 3
curl -s -u "admin:$HARBOR_ADMIN_PASSWORD" \
  http://localhost:8080/api/v2.0/projects/lolday/repositories | jq '.[].name'
# Expect: "lolday/lolday-backend", "lolday/build-helper"
kill $PF_PID
```

---

## 6. Update backend to use Harbor image

```bash
helm upgrade lolday ./charts/lolday \
  -n lolday --reuse-values \
  --set backend.image=harbor.harbor.svc:80/lolday/lolday-backend:phase3
```

Backend pod restarts. Watch:
```bash
kubectl -n lolday rollout status deployment/backend
kubectl -n lolday logs deployment/backend --tail=50 -f
# Ctrl+C when you see: "Application startup complete" + "build reconciler started"
```

If image pull fails with "unauthorized": means Harbor's `lolday` project isn't public. Check Harbor UI → Projects → lolday → Configuration → "Public" checkbox. (`init_harbor` should have set this, but verify.)

---

## 7. Verify platform health

```bash
kubectl -n lolday get pods           # all Running
kubectl -n harbor get pods           # all Running

# Backend health endpoint
kubectl -n lolday port-forward svc/backend 8000:8000 &
BE_PID=$!; sleep 2
curl -s http://localhost:8000/api/v1/health
# {"status":"ok"}
kill $BE_PID

# Harbor UI (browser via port-forward)
kubectl -n harbor port-forward svc/harbor 8080:80 &
# open http://localhost:8080 — login: admin / $HARBOR_ADMIN_PASSWORD
# Check: Projects tab has detectors / detectors-cache / lolday
```

---

## Rollback

### Full rollback (wipe Phase 3)
```bash
helm uninstall lolday -n lolday
helm uninstall harbor -n harbor 2>/dev/null || true   # in case it became its own release
kubectl delete pvc -n lolday --all
kubectl delete pvc -n harbor --all
kubectl delete namespace lolday harbor

# Revert registries.yaml
sudo ls /etc/rancher/k3s/registries.yaml.bak.*
# pick the most recent backup, copy back
sudo cp /etc/rancher/k3s/registries.yaml.bak.<timestamp> /etc/rancher/k3s/registries.yaml
sudo systemctl restart k3s
sudo systemctl is-active k3s    # active
```

### Partial rollback (backend image only)
```bash
helm upgrade lolday ./charts/lolday \
  -n lolday --reuse-values \
  --set backend.image=localhost:5000/lolday-backend:latest
```

---

## Common gotchas

| Symptom | Cause | Fix |
|---------|-------|-----|
| Backend CrashLoop with `fernet_key` error | FERNET_KEY malformed (not base64 32-byte) | Regenerate per step 1 |
| Harbor core pod stuck `Init` | PVC not bound | `kubectl describe pod -n harbor harbor-core-*` — check StorageClass; default `local-path` should work on K3s |
| Harbor login 401 | Wrong password OR Harbor still initializing | Wait 3min after all pods Ready; retry |
| `patch-k3s-registries.sh` fails with "Failed to read Harbor ClusterIP" | Harbor not deployed yet | Complete step 3 first |
| Image push timeout | Harbor database / Redis not ready | `kubectl -n harbor get pods` — wait for all Ready |
| Backend log: `harbor init failed` repeatedly | `robot$build-pusher` creation race | Restart backend pod: `kubectl -n lolday rollout restart deployment/backend` |
| K3s restart broke SSH | Rare but catastrophic | Console access (if available) → `systemctl restart k3s` OR boot from backup |

---

## After Task 17 completes → proceed to Task 18 E2E

See `phase3-e2e-checklist.md` (in this same `docs/phase-history/` directory).
