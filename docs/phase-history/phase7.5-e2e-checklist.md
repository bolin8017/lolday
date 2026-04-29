# Phase 7.5 E2E Checklist

Hardening pass (Alembic migrations + RBAC narrow + stale-Volcano alert).
Run after `bash scripts/deploy.sh` completes.

## Alembic migration hook

- [ ] Helm pre-upgrade Job `alembic-upgrade` ran successfully:

  ```bash
  kubectl -n lolday get jobs alembic-upgrade 2>/dev/null || echo "auto-cleaned via hook-succeeded"
  kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday \
    -c 'SELECT version_num FROM alembic_version;'
  ```

  Expect: a single revision id matching the latest file in
  `backend/migrations/versions/`.

- [ ] Backend pod's lifespan schema-version check passed:
  ```bash
  kubectl -n lolday logs deploy/backend --tail=30 | grep -iE "schema|alembic" || echo "no schema log — means check passed silently"
  ```
  A mismatch would have crash-looped the pod.

## RBAC narrow

- [ ] Backend can still read pods in its own namespace:

  ```bash
  kubectl -n lolday exec deploy/backend -- \
    curl -sH "Authorization: Bearer $(get-token)" \
    http://backend.lolday.svc:8000/api/v1/cluster/gpu-status
  ```

  Expect `{total:2,in_use:0,idle:2}` (or similar based on workload).

- [ ] Backend SA can NOT read pods in kube-system (negative test):

  ```bash
  kubectl -n lolday auth can-i list pods --namespace kube-system \
    --as=system:serviceaccount:lolday:backend
  ```

  Expect `no` — the Phase 7.4 over-grant was removed.

- [ ] Backend SA CAN list nodes (cluster-scope, still needed for GPU count):
  ```bash
  kubectl auth can-i list nodes \
    --as=system:serviceaccount:lolday:backend
  ```
  Expect `yes`.

## Stale-Volcano alert

- [ ] Gauge exposed on `/metrics`:

  ```bash
  kubectl -n lolday exec deploy/backend -- \
    curl -s http://backend.lolday.svc:8000/metrics | grep lolday_volcano_pending_stale
  ```

  Expect `lolday_volcano_pending_stale 0.0` (no stale jobs under normal operation).

- [ ] Alert rule loaded by Prometheus:

  ```bash
  kubectl -n monitoring port-forward svc/kps-prometheus 9090:9090 &
  curl -s http://localhost:9090/api/v1/rules | jq '.data.groups[].rules[] | select(.name == "VolcanoJobsStuckPending")'
  ```

  Expect the alert definition with `expr: lolday_volcano_pending_stale > 5`.

- [ ] (Optional) Synthetic test — create 6 Volcano Jobs with old
      `creationTimestamp`, wait 11 min, verify Discord warning fires. Only
      run during scheduled chaos session; delete synthetic CRs afterward.

## Sign-off

- [ ] Date: <!-- YYYY-MM-DD -->
- [ ] Verifier: <!-- name -->
