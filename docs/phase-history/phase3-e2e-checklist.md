# Phase 3 E2E Smoke Test Checklist (Task 18)

**Prerequisite:** Task 17 deploy complete — Harbor healthy, backend using Phase 3 image, `patch-k3s-registries.sh` applied.

Tests the full detector lifecycle against the real `upxelfdet` repo (github.com/bolin8017/upxelfdet). Uses `kubectl port-forward` — no external DNS/TLS required.

**Required env vars from Task 17:**
- `HARBOR_ADMIN_PASSWORD`
- `ADMIN_EMAIL` + `ADMIN_PASSWORD`
- A valid GitHub PAT for `bolin8017` with `public_repo` scope (repo is public, PAT just avoids GitHub 60req/hr rate limit)

```bash
export GITHUB_PAT="ghp_your_token_here"
```

---

## Setup

```bash
# Port-forward backend for API access
kubectl -n lolday port-forward svc/backend 8000:8000 &
BE_PID=$!
sleep 2

# 1. Login as admin
export TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=$ADMIN_EMAIL&password=$ADMIN_PASSWORD" \
  -H "Content-Type: application/x-www-form-urlencoded" | jq -r .access_token)
[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ] || { echo "admin login failed"; exit 1; }
echo "Admin OK: ${TOKEN:0:20}..."

# 2. Create dev user via self-register
curl -s -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "dev@lolday.dev", "password": "DevPass123!"}' | jq

# 3. Promote to developer role (admin API)
DEV_ID=$(curl -s http://localhost:8000/api/v1/admin/users \
  -H "Authorization: Bearer $TOKEN" | jq -r '.items[] | select(.email=="dev@lolday.dev") | .id')
[ -n "$DEV_ID" ] || { echo "dev user not found"; exit 1; }

curl -s -X PATCH http://localhost:8000/api/v1/users/$DEV_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "developer"}' | jq

# 4. Login as dev
export DEV_TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=dev@lolday.dev&password=DevPass123!" \
  -H "Content-Type: application/x-www-form-urlencoded" | jq -r .access_token)
echo "Dev OK: ${DEV_TOKEN:0:20}..."
```

---

## Test 1: Register PAT

```bash
curl -s -X PUT http://localhost:8000/api/v1/users/me/git-credential \
  -H "Authorization: Bearer $DEV_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"provider\": \"github\", \"token\": \"$GITHUB_PAT\"}" | jq

# Expected: {provider: "github", token_hint: "ghp_...xxxx", created_at: ..., updated_at: ...}

# Verify hint only, no token:
curl -s http://localhost:8000/api/v1/users/me/git-credential \
  -H "Authorization: Bearer $DEV_TOKEN" | jq
# must NOT contain "token" key
```

**Pass criteria:** 200 response, `token_hint` present, `token` field absent.

---

## Test 2: Register `upxelfdet`

```bash
curl -s -X POST http://localhost:8000/api/v1/detectors \
  -H "Authorization: Bearer $DEV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"git_url": "https://github.com/bolin8017/upxelfdet"}' | jq

# Expected (may take 5-15 seconds — backend shallow-clones the repo):
# { id: "<uuid>", name: "upxelfdet" | derived from pyproject.toml,
#   display_name: ..., git_url: "https://github.com/bolin8017/upxelfdet.git",
#   owner_id: "<dev-uuid>", created_at: ... }

# Save detector_id
export DETECTOR_ID=$(curl -s http://localhost:8000/api/v1/detectors \
  -H "Authorization: Bearer $DEV_TOKEN" | jq -r '.items[] | select(.git_url | contains("upxelfdet")) | .id')
echo "DETECTOR_ID=$DETECTOR_ID"
```

**Pass criteria:** 201 response, detector listed in GET /detectors, git_url normalized with trailing `.git`.

**If 400 with code `dockerfile_missing`:** upxelfdet repo doesn't have a Dockerfile yet. Add one before proceeding (see appendix A).

---

## Test 3: List available tags

```bash
curl -s http://localhost:8000/api/v1/detectors/$DETECTOR_ID/available-tags \
  -H "Authorization: Bearer $DEV_TOKEN" | jq
# Expected: array of {name, commit_sha} from GitHub — at least one tag if upxelfdet has been tagged
```

**If empty:** upxelfdet has no tags yet. Create one:
```bash
cd /home/bolin8017/Documents/repositories/upxelfdet
git tag v0.1.0 && git push --tags
```

**Pass criteria:** 200 response, at least one tag returned.

---

## Test 4: Trigger a build

```bash
# Pick the first available tag
export TAG=$(curl -s http://localhost:8000/api/v1/detectors/$DETECTOR_ID/available-tags \
  -H "Authorization: Bearer $DEV_TOKEN" | jq -r '.[0].name')
echo "Building tag: $TAG"

BUILD=$(curl -s -X POST http://localhost:8000/api/v1/detectors/$DETECTOR_ID/builds \
  -H "Authorization: Bearer $DEV_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"git_tag\": \"$TAG\"}")
echo $BUILD | jq
export BUILD_ID=$(echo $BUILD | jq -r .id)
echo "BUILD_ID=$BUILD_ID"
```

**Pass criteria:** 201 response, `status: cloning`, `k8s_job_name` non-null.

**If 409 build_in_flight:** a previous build for same tag is still running. Wait or cancel it.

---

## Test 5: Observe build progress

```bash
# Watch in terminal (Ctrl+C when done):
watch -n 5 "curl -s http://localhost:8000/api/v1/detectors/$DETECTOR_ID/builds/$BUILD_ID \
  -H 'Authorization: Bearer $DEV_TOKEN' | jq '{status, failure_reason, trivy_critical, trivy_high}'"
```

Expected transitions (may take 5-15 minutes total):
1. `cloning` (~10s) — git clone in init container
2. `validating` (~1-3min) — pip install + reflection checks in build-helper image
3. `building` (~3-10min) — Kaniko builds Dockerfile, pushes to Harbor
4. `scanning` (~30s-2min) — Harbor's Trivy scans image
5. **Terminal:** `succeeded` (trivy_critical=0, high=0) OR `cve_blocked` (if the base image has CVEs) OR `failed` (if validate/build errored)

**Simultaneously monitor K8s Job:**
```bash
kubectl -n lolday get jobs -l app=lolday-build
kubectl -n lolday logs -l app=lolday-build --all-containers=true --tail=100
```

**Pass criteria:** status reaches `succeeded` within 20 minutes.

**If `cve_blocked`:** Check `trivy_critical` / `trivy_high` counts. Rebuild `upxelfdet` Dockerfile with newer base image if CVEs are in the base. Iterate.

**If `failed` with `validation.missing_base_detector`:** check upxelfdet's module structure (BaseDetector import must be reachable from a top-level package).

---

## Test 6: Verify version recorded in DB

```bash
curl -s http://localhost:8000/api/v1/detectors/$DETECTOR_ID/versions \
  -H "Authorization: Bearer $DEV_TOKEN" | jq
# Expected: items array with one entry matching $TAG

curl -s http://localhost:8000/api/v1/detectors/$DETECTOR_ID/versions/$TAG \
  -H "Authorization: Bearer $DEV_TOKEN" | jq
# Expected: VersionDetailRead with config_schema populated
```

**Pass criteria:**
- Version exists with `status: active`
- `harbor_image` = `harbor.harbor.svc:80/detectors/upxelfdet:$TAG`
- `image_digest` starts with `sha256:`
- `config_schema` is a non-empty JSON Schema (from maldet BaseDetectorConfig + upxelfdet overrides)
- `git_sha` is 40-char hex

---

## Test 7: Verify image in Harbor

```bash
kubectl port-forward -n harbor svc/harbor 8080:80 &
HB_PID=$!; sleep 2

# Via API
curl -s -u "admin:$HARBOR_ADMIN_PASSWORD" \
  "http://localhost:8080/api/v2.0/projects/detectors/repositories/upxelfdet/artifacts?with_scan_overview=true" | jq

kill $HB_PID
```

**Pass criteria:**
- Artifact with tag `$TAG` exists
- `scan_overview` contains the vulnerability summary
- `scan_status: Success`

**Alternative:** browser UI at http://localhost:8080 → Projects → detectors → upxelfdet → artifact view → Vulnerabilities tab.

---

## Test 8: Cleanup

```bash
# Cancel any in-flight builds
curl -s -X POST http://localhost:8000/api/v1/detectors/$DETECTOR_ID/builds/$BUILD_ID/cancel \
  -H "Authorization: Bearer $DEV_TOKEN" -o /dev/null -w "%{http_code}\n"
# 204 if already terminal (no-op)

# Soft-delete detector (triggers Harbor artifact cleanup)
curl -s -X DELETE http://localhost:8000/api/v1/detectors/$DETECTOR_ID \
  -H "Authorization: Bearer $DEV_TOKEN" -o /dev/null -w "%{http_code}\n"
# 204

# Verify 404
curl -s http://localhost:8000/api/v1/detectors/$DETECTOR_ID \
  -H "Authorization: Bearer $DEV_TOKEN" -o /dev/null -w "%{http_code}\n"
# 404

# Stop port-forward
kill $BE_PID 2>/dev/null || true
```

**Pass criteria:** delete returns 204, subsequent GET returns 404, Harbor artifact eventually gone (check Harbor UI — may take a moment).

---

## Pass summary

All 8 tests passing = Phase 3 end-to-end working. You can then:
1. Mark Task 18 complete
2. Squash merge `dev` → `main` per project convention
3. Proceed to Phase 4 design

---

## Appendix A — If upxelfdet lacks a Dockerfile

Minimal example at repo root:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
ENTRYPOINT ["python", "-m", "upxelfdet"]
```

Commit + tag + push:
```bash
cd /home/bolin8017/Documents/repositories/upxelfdet
git add Dockerfile && git commit -m "add Dockerfile for lolday build pipeline"
git tag v0.1.0 && git push && git push --tags
```

Retry Test 2.

---

## Appendix B — Debug techniques

**Dump build logs from K8s Job:**
```bash
kubectl -n lolday logs -l lolday.io/build-id=$BUILD_ID --all-containers=true
```

**Exec into running init container (before it terminates):**
```bash
kubectl -n lolday get pods -l lolday.io/build-id=$BUILD_ID
kubectl -n lolday debug pod/<pod-name> -c validate --image=ubuntu --attach
```

**Check Harbor scan status directly:**
```bash
kubectl -n lolday exec deployment/backend -- python -c "
import asyncio
from app.services.harbor import HarborClient
from app.config import settings
async def main():
    h = HarborClient(settings.HARBOR_URL, settings.HARBOR_ADMIN_USERNAME, settings.HARBOR_ADMIN_PASSWORD)
    digest = await h.get_artifact_digest('detectors', 'upxelfdet', '$TAG')
    scan = await h.get_scan('detectors', 'upxelfdet', digest)
    print(scan)
asyncio.run(main())
"
```

**Force a stuck build to fail:**
```bash
BUILD_ID=<uuid>
kubectl -n lolday delete job -l lolday.io/build-id=$BUILD_ID
# Reconciler will mark it FAILED within 10s
```

**Reset entire Phase 3 state (nuclear):** See `phase3-deploy-runbook.md` § Rollback (in this same `docs/phase-history/` directory).
