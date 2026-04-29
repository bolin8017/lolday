# Phase 4 E2E Smoke Test Checklist

**Purpose:** Validate end-to-end dataset + job + MLflow + Model Registry pipeline.

**Prerequisites:**

- Phase 3 deploy + E2E passed (upxelfdet successfully built to Harbor previously)
- Sample directory populated at `/data/samples/<sha256[:2]>/<sha256>` (flat layout per upxelfdet convention; at least ~20 samples matching `file_name`s in the test dataset)
- Phase 4 deploy completed (MLflow pod Running, backend Ready) — see `scripts/deploy.sh`
- **Upstream dependencies merged and released:**
  - `islab-malware-detector` PR #4 merged + tag `v0.5.0` pushed
  - `upxelfdet` PR #2 merged + tag `v0.5.0` pushed
- upxelfdet v0.5.0 (or later MLflow-aware version) built and stored in Harbor via Phase 3 build pipeline:
  - Trigger: `POST /api/v1/detectors/<upxelfdet-id>/builds` with `{"git_tag": "v0.5.0"}`
  - Wait for `SUCCEEDED`
- Authenticated HTTP session (save JWT from login response into `$TOKEN`)

Port-forward to reach backend from dev machine:

```bash
kubectl -n lolday port-forward svc/backend 8000:8000 &
```

---

## 1. Dataset Config CRUD

- [ ] Upload a small dataset config (subset of Malware202403_info.csv, ~100 rows matching samples on disk)

```bash
curl -X POST http://localhost:8000/api/v1/datasets \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"e2e-100\", \"csv_content\": \"$(cat /tmp/e2e-dataset.csv | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read())[1:-1])')\"}"
```

Expected: 201 with `sample_count=100`, `csv_checksum` non-empty.

- [ ] Upload a test split (50 rows)
- [ ] `GET /api/v1/datasets` returns both
- [ ] `GET /api/v1/datasets/{id}` returns metadata (no CSV content)
- [ ] `GET /api/v1/datasets/{id}/csv` returns raw CSV

## 2. Train Job

- [ ] Submit train job:

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "train",
    "detector_version_id": "<upxelfdet-v0.5.0-id>",
    "train_dataset_id": "<train-ds-id>",
    "test_dataset_id": "<test-ds-id>",
    "params": {"seed": 42}
  }'
```

Expected: 202, returns `job_id`, `mlflow_run_id`, `status=preparing`.

- [ ] Poll status:

```bash
watch -n 2 "curl -s -H 'Authorization: Bearer $TOKEN' http://localhost:8000/api/v1/jobs/<job_id> | jq '.status, .failure_reason'"
```

Expected transitions: `preparing` → `running` → `succeeded` within ~10 min for 100 samples.

- [ ] Check K8s:

```bash
kubectl -n lolday get jobs -l lolday.job-type=train
kubectl -n lolday get pods -l lolday.job-type=train
kubectl -n lolday describe pod -l lolday.job-id=<job_id>
```

Expected: Pod transitioned `ContainerCreating` → `Running` → `Succeeded`; init containers finished 0.

- [ ] Check MLflow UI:

```bash
kubectl -n lolday port-forward svc/mlflow 5000:5000 &
# Open http://localhost:5000 in browser
```

Expected: experiment `detector:<upxelfdet-id>:v0.5.0` has 1 FINISHED run, with:

- flat params (model.type, vectorize.method, etc.)
- metrics (if autolog caught sklearn's SVM fit)
- artifacts: `config.json`, `model/` with pickled model files

- [ ] Verify model_version row created:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/models | jq
```

Expected: one entry `{name: "upxelfdet", latest_version: 1}`.

## 3. Evaluate Job

- [ ] Submit evaluate:

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "evaluate",
    "detector_version_id": "<upxelfdet-v0.5.0-id>",
    "test_dataset_id": "<test-ds-id>",
    "source_model_version_id": "<mv-id-from-step-2>",
    "params": {}
  }'
```

- [ ] Wait for `succeeded`.

- [ ] Check `GET /api/v1/jobs/{id}`:

Expected: `summary_metrics` populated with `{accuracy, precision, recall, f1, confusion_matrix?}`.

## 4. Predict Job

- [ ] Submit predict:

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "predict",
    "detector_version_id": "<upxelfdet-v0.5.0-id>",
    "predict_dataset_id": "<predict-ds-id>",
    "source_model_version_id": "<mv-id>",
    "params": {}
  }'
```

- [ ] Wait for `succeeded`.

- [ ] Download prediction artifact via MLflow proxy:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/runs/<predict-run-id>/artifacts/download?path=prediction/prediction.csv" > /tmp/pred.csv
head /tmp/pred.csv
```

Expected: CSV with file_name + prediction columns.

## 5. Model Registry Transitions

- [ ] Promote v1 to Staging:

```bash
curl -X POST http://localhost:8000/api/v1/models/upxelfdet/versions/1/transition \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"to_stage": "Staging", "comment": "smoke test"}'
```

- [ ] Promote to Production:

```bash
curl -X POST http://localhost:8000/api/v1/models/upxelfdet/versions/1/transition \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"to_stage": "Production"}'
```

- [ ] Verify MLflow reflects the stage:

```bash
curl -s http://localhost:5000/api/2.0/mlflow/registered-models/get -d '{"name": "upxelfdet"}' | jq
```

Expected: `latest_versions[0].current_stage = "Production"`.

- [ ] Train a new version (v2), promote to Production; verify v1 auto-archives.

## 6. Error Paths

- [ ] Submit with bad params → expect 422:

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"type": "train", "detector_version_id": "...", "train_dataset_id": "...", "params": {"seed": "not-an-int"}}'
```

- [ ] Submit duplicate within 5 min → expect 409.
- [ ] Exceed `JOB_PER_USER_CONCURRENCY` (2) → expect 429.
- [ ] Cancel running job:

```bash
curl -X POST http://localhost:8000/api/v1/jobs/<id>/cancel \
  -H "Authorization: Bearer $TOKEN"
```

Expected: Pod deleted within 15s; job row `cancelled`.

- [ ] Delete dataset with active job → expect 409.

## 7. NetworkPolicy Enforcement

- [ ] Shell into a running job pod (while it's active):

```bash
kubectl -n lolday exec -it <job-pod> -c detector -- sh -c 'curl --max-time 5 -s http://harbor.harbor.svc:80/ || echo BLOCKED'
```

Expected: `BLOCKED` (NetworkPolicy denies egress to Harbor).

```bash
kubectl -n lolday exec -it <job-pod> -c detector -- sh -c 'curl --max-time 5 -s http://mlflow.lolday.svc:5000/health'
```

Expected: `OK`.

```bash
kubectl -n lolday exec -it <job-pod> -c detector -- sh -c 'curl --max-time 5 -s https://github.com || echo BLOCKED'
```

Expected: `BLOCKED` (no internet egress).

## 8. SSH Safety Check

- [ ] After all the above, confirm SSH on port 9453 still responsive:

```bash
nc -zv server30 9453
```

Expected: `Connection to server30 9453 port [tcp/*] succeeded!`

- [ ] K3s still healthy:

```bash
ssh -p 9453 server30 'sudo systemctl is-active k3s'
```

Expected: `active`.

## Sign-off

- [ ] All dataset tests pass
- [ ] All 3 job types succeed
- [ ] Model Registry transitions work and archive on Production promotion
- [ ] Error paths return correct status codes
- [ ] NetworkPolicy blocks unintended egress
- [ ] SSH unaffected

On successful sign-off, Phase 4 is ready to squash-merge to `main`.

---

## Pre-deployment checklist (T19 operational steps)

Before running the E2E above, these images must be built and pushed to Harbor:

1. **Build + push job-helper image:**

```bash
cd /home/bolin8017/Documents/repositories/lolday
docker build -t harbor.harbor.svc.cluster.local:80/lolday/job-helper:v1 charts/lolday/helpers/job-helper/

# Login to Harbor with robot account
docker login harbor.harbor.svc.cluster.local:80 \
  -u 'robot$build-pusher' \
  -p "$(kubectl -n lolday get secret harbor-push-cred -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | python3 -c 'import json,sys; print(json.load(sys.stdin)["auths"]["harbor.harbor.svc:80"]["password"])')"

docker push harbor.harbor.svc.cluster.local:80/lolday/job-helper:v1
```

2. **Rebuild + push backend image** (backend code changed significantly in Phase 4):

```bash
docker build -t harbor.harbor.svc.cluster.local:80/lolday/lolday-backend:latest backend/
docker push harbor.harbor.svc.cluster.local:80/lolday/lolday-backend:latest
kubectl -n lolday rollout restart deploy/backend
kubectl -n lolday rollout status deploy/backend --timeout=120s
```

3. **Verify Alembic migration applied:**

```bash
kubectl -n lolday exec deploy/backend -- alembic current
```

Expected: the Phase 4 migration revision as head. If not applied automatically on backend startup, run manually:

```bash
kubectl -n lolday exec deploy/backend -- alembic upgrade head
```

4. **Deploy Phase 4 Helm changes:**

```bash
export MLFLOW_DB_PASSWORD=$(openssl rand -base64 32 | tr -d '=+/')
bash scripts/deploy.sh
```
