# Phase 11b E2E Checklist

Verifies the Phase 11b detector-contract rewrite end-to-end on server30.
Run after `bash scripts/deploy.sh` completes.

**Prerequisites:**
- Phase 11b PR merged to `main` (commit on main with Chart 0.13.0 + backend phase11b + job-helper v3)
- `maldet` >= 1.0 installed in operator's local venv (`pip install maldet`)
- `~/.lolday-secrets.env` sourced
- `docker` available; operator in the `docker` group (or prepared to `sudo`)
- Harbor robot credentials accessible via existing `harbor-push-cred` Secret
- K3s running, SSH on 9453 not disrupted, backend pod not CrashLoopBackOff

## 1. Build + push backend image `phase11b`

```bash
cd ~/Documents/repositories/lolday
docker build -t harbor.lolday.svc.cluster.local:80/lolday/lolday-backend:phase11b backend/
# Reuse Harbor push cred
HARBOR_PW=$(kubectl -n lolday get secret harbor-push-cred -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | jq -r '.auths[].auth' | base64 -d | cut -d: -f2)
docker login harbor.lolday.svc.cluster.local:80 -u 'robot$build-pusher' -p "$HARBOR_PW"
docker push harbor.lolday.svc.cluster.local:80/lolday/lolday-backend:phase11b
```

- [ ] `docker push` completes (all layers pushed)

## 2. Build + push job-helper image `v3`

```bash
cd ~/Documents/repositories/lolday/charts/lolday/helpers/job-helper
docker build -t harbor.lolday.svc.cluster.local:80/lolday/job-helper:v3 .
docker push harbor.lolday.svc.cluster.local:80/lolday/job-helper:v3
```

- [ ] `docker push` completes

## 3. Deploy chart 0.13.0

```bash
cd ~/Documents/repositories/lolday
source ~/.lolday-secrets.env
bash scripts/deploy.sh
```

- [ ] `alembic upgrade head` hook Job runs + succeeds:
  ```bash
  kubectl -n lolday get jobs | grep alembic
  kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday \
    -c 'SELECT version_num FROM alembic_version;'
  ```
  Expect: a single revision id matching the latest file in `backend/migrations/versions/`.
- [ ] Backend Deployment rolls to `phase11b` with no CrashLoopBackOff:
  ```bash
  kubectl -n lolday rollout status deploy/backend
  kubectl -n lolday get pods | grep backend
  ```
- [ ] Helm release at revision N+1 with chart `lolday-0.13.0`:
  ```bash
  helm -n lolday history lolday | tail -3
  ```

## 4. Verify DB schema

```bash
kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday -c '\d job_events'
kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday -c '\d detector_version' | grep manifest
```

- [ ] `job_events` table exists with columns: `id`, `job_id`, `ts`, `kind`, `payload`, `received_at`
- [ ] `ix_job_events_job_ts` index present:
  ```bash
  kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday \
    -c '\di job_events*'
  ```
  Expect `ix_job_events_job_ts` in output.
- [ ] `detector_version.manifest` column is JSONB nullable:
  ```bash
  kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday \
    -c "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name='detector_version' AND column_name='manifest';"
  ```
  Expect `data_type = jsonb`, `is_nullable = YES`.

## 5. Scaffold + build a smoketest detector

```bash
cd /tmp
# Produce a minimal detector from the maldet scaffold
maldet scaffold --template rf --name smoketest --out ./smoketest
cd smoketest

# Replace features.py with a trivial extractor (avoids real ELF parse for smoke)
cat > src/smoketest/features.py <<'EOF'
import numpy as np
from maldet.types import Sample

class Text256Extractor:
    output_shape = (4,)
    dtype = "float32"

    def __init__(self, size=256, pad_value=0):
        self.size = size
        self.pad_value = pad_value

    def extract(self, sample: Sample):
        if sample.label == "Malware":
            return np.ones(4, dtype=np.float32)
        return np.zeros(4, dtype=np.float32)
EOF

# Install + validate locally
pip install -e .
maldet check
maldet describe --format json
```

- [ ] `maldet check` reports `OK`
- [ ] `maldet describe` prints detector manifest JSON (contains `detector.name = "smoketest"`)

## 6. Build + push smoketest detector image with manifest label

```bash
cd /tmp/smoketest
MALDET_MANIFEST_B64=$(maldet describe --format json | base64 -w0)

docker build \
  --build-arg MALDET_NAME=smoketest \
  --build-arg MALDET_VERSION=0.1.0 \
  --build-arg MALDET_FRAMEWORK=sklearn \
  --build-arg MALDET_MANIFEST_B64="$MALDET_MANIFEST_B64" \
  --build-arg GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo nocommit) \
  -t harbor.lolday.svc.cluster.local:80/lolday/smoketest:v0.1.0 .

docker push harbor.lolday.svc.cluster.local:80/lolday/smoketest:v0.1.0
```

- [ ] `docker build` produces image with `io.maldet.manifest` label set
- [ ] Label is non-empty base64:
  ```bash
  docker inspect harbor.lolday.svc.cluster.local:80/lolday/smoketest:v0.1.0 \
    | jq -r '.[0].Config.Labels["io.maldet.manifest"]' | wc -c
  ```
  Expect: > 50 characters.
- [ ] `docker push` succeeds

## 7. Register smoketest detector in lolday + verify manifest persisted

Use lolday UI (https://lolday.connlabai.com) or curl against backend.

First obtain a session cookie via the SSO flow (Cloudflare Access), then:

```bash
BACKEND_URL=https://lolday.connlabai.com
# COOKIE must be set from your browser session (copy from DevTools → Application → Cookies)

# Register the smoketest detector
curl -b "$COOKIE" -X POST "$BACKEND_URL/api/v1/detectors" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "smoketest",
    "display_name": "Smoketest RF",
    "git_url": "file:///tmp/smoketest",
    "description": "phase 11b smoketest"
  }'
```

- [ ] Smoketest appears in `/api/v1/detectors`:
  ```bash
  curl -b "$COOKIE" "$BACKEND_URL/api/v1/detectors" | jq '.[] | select(.name=="smoketest")'
  ```

**If the lolday build pipeline does not yet push manifest-labeled images**: the current
`services/build.py` builds detectors via BuildKit with a vanilla Dockerfile that does not
know about `io.maldet.manifest`. For this smoketest, skip the build pipeline and insert the
`DetectorVersion` row directly via `psql` so Phase 11b's job submission path gets a manifest
to read. Phase 11c / 11d will harden the build pipeline to emit the label.

```bash
# Manual fallback: insert DetectorVersion row with manifest populated
MANIFEST_JSON=$(echo "$MALDET_MANIFEST_B64" | base64 -d)
kubectl -n lolday exec -it postgresql-0 -- psql -U lolday -d lolday -c \
  "INSERT INTO detector_version (detector_id, tag, image_ref, manifest, created_at)
   VALUES (<detector-id>, 'v0.1.0',
           'harbor.lolday.svc.cluster.local:80/lolday/smoketest:v0.1.0',
           '$MANIFEST_JSON'::jsonb,
           now())
   RETURNING id;"
```

- [ ] `detector_version.manifest` JSONB is populated:
  ```bash
  kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday \
    -c "SELECT manifest->'detector'->>'name' FROM detector_version ORDER BY created_at DESC LIMIT 1;"
  ```
  Expect: `smoketest`.

## 8. Submit a training job + watch events stream live

```bash
# DV_ID = detector_version id from section 7
# DS_ID = an existing train dataset id
curl -b "$COOKIE" -X POST "$BACKEND_URL/api/v1/jobs" \
  -H 'Content-Type: application/json' \
  -d "{
    \"type\": \"train\",
    \"detector_version_id\": \"$DV_ID\",
    \"train_dataset_id\": \"$DS_ID\",
    \"params\": {},
    \"resource_profile\": \"standard\"
  }"
```

- [ ] Job accepted (HTTP 201); capture `JOB_ID` from response
- [ ] Pod has TWO containers — `detector` and `event-tailer`:
  ```bash
  kubectl -n lolday get pods | grep job-train-
  kubectl -n lolday describe pod <job-pod> | grep -A2 "Containers:"
  ```
- [ ] In a browser, open `https://lolday.connlabai.com/jobs/$JOB_ID`; the live metric chart appears as the detector runs
- [ ] WebSocket connects (Network tab: `/api/v1/jobs/<id>/events` protocol switches to `101 Switching Protocols`)
- [ ] `stage_begin`, `data_loaded`, `metric` events stream in during the run
- [ ] On completion: `stage_end status=success` event arrives in the browser

## 9. Verify reconciler trusts stage_end (event-based termination)

- [ ] Job row transitions to `SUCCEEDED` within ~15 s of the `stage_end success` event:
  ```bash
  kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday \
    -c "SELECT id, status, finished_at FROM job WHERE id='$JOB_ID';"
  ```
  Expect `status = SUCCEEDED` and `finished_at` is set.
- [ ] Backend logs show event-based termination, not phase-based polling:
  ```bash
  kubectl -n lolday logs deploy/backend | grep reconciler | grep -i "stage_end\|event"
  ```
  Must NOT show `phase=Completed` as the termination trigger for this job.

## 10. Verify historical event retrieval

```bash
curl -b "$COOKIE" "$BACKEND_URL/api/v1/jobs/$JOB_ID/events?limit=100"
```

- [ ] Returns HTTP 200 with `events[]` containing the full stream (stage_begin through stage_end)
- [ ] Events are in chronological order (`ts` ascending)
- [ ] `next_since` is `null` if total events < 100, OR a cursor string if many events

## 11. Verify v0 detector rejection (negative test)

Build an image WITHOUT the `io.maldet.manifest` label (e.g., reuse any pre-phase11b image
or build a blank one):

```bash
docker build -t harbor.lolday.svc.cluster.local:80/lolday/v0test:nolabel - <<'EOF'
FROM python:3.11-slim
RUN echo "no manifest label"
EOF
docker push harbor.lolday.svc.cluster.local:80/lolday/v0test:nolabel
```

Submit a build / insert a `DetectorVersion` row pointing at this image, then trigger a job.

- [ ] Reconciler marks the build FAILED with `failure_reason = "manifest_label_missing"`:
  ```bash
  kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday \
    -c "SELECT status, failure_reason FROM job ORDER BY created_at DESC LIMIT 5;"
  ```
- [ ] Prometheus metric increments:
  ```bash
  kubectl -n lolday exec deploy/backend -- \
    curl -s http://backend.lolday.svc:8000/metrics \
    | grep 'lolday_backend_errors_total{.*manifest_missing'
  ```
  Expect value >= 1.

## 12. SSH + tunnel + core-service sanity

- [ ] SSH still works:
  ```bash
  ssh -p 9453 <user>@server30 "echo ok"
  ```
- [ ] `https://lolday.connlabai.com/` still responds (Cloudflare Tunnel + Zero Trust)
- [ ] Prometheus reachable:
  ```bash
  kubectl -n monitoring port-forward svc/kps-prometheus 9090:9090 &
  curl -s http://localhost:9090/-/healthy
  ```
- [ ] Grafana and MLflow UI still reachable via port-forward

---

## Known limitations (expected, not blockers)

- The lolday build pipeline (`services/build.py`) does NOT yet build images with the
  `io.maldet.manifest` label. Phase 11c / 11d will update the build pipeline so scaffolded
  detectors can be registered → built → scanned → promoted fully automatically. Phase 11b
  smoke path requires manual image builds that inject the label via `--build-arg`.
- v0 detector images (any Harbor artifact pre-phase11b) will all be rejected at build time
  once Phase 11b deploys. This is intentional — no v0 detectors were in production use.

## Sign-off

- [ ] Date: <!-- YYYY-MM-DD -->
- [ ] Verifier: <!-- name -->
