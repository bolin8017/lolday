# Phase 2: Volcano per-user queue + capability cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single shared `lolday-training` queue with per-user `lolday-u-<id12>` queues so Volcano's already-enabled `drf` + `proportion` plugins can fairly share GPUs across users. Closes spec §5.3 (single-queue FIFO unfairness) + §6.3 + §7 Phase 2.

**Architecture:** Backend creates one Volcano Queue per user the first time they POST a job (idempotent on 409). Each user queue: `weight=1, reclaimable=true, capability={cpu=8, memory=30Gi, nvidia.com/gpu=2}` — these match the namespace-level `lolday-jobs-quota`, so a single user can't exceed it via queue alone. The original `lolday-training` queue keeps existing as a fallback (any job that lacks an owner / pre-Phase-2 reconciliation), but new submissions route to per-user queues. Volcano scheduler config (`drf` + `proportion`) is **already enabled** by the upstream sub-chart defaults — verified via `kubectl -n lolday get cm lolday-scheduler-configmap -o yaml` — no scheduler change needed.

**Tech Stack:** Volcano `scheduling.volcano.sh/v1beta1.Queue` (cluster-scoped CRD), `kubernetes.client.CustomObjectsApi`, FastAPI sync-in-async handler.

**Spec:** `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` §5.3, §6.3, §7 Phase 2. Resolution to OQ-1 (capability semantics): set `nvidia.com/gpu: "2"` per user queue — this is the SUM cap (Volcano docs §queue.capability), so a single user can run one GPU2 job OR two GPU1 jobs concurrently. Combined with DRF this still gives fair-share between contending users.

**Pre-requisite:** Phase 0 (PR #86), Phase 1 (PR #87 + #88), Phase 4 (PR #89) all merged + applied. Volcano scheduler config already has `drf` + `proportion` enabled (verified 2026-05-05).

---

## Reference: queue capability + name format

| Field                                   | Value                            | Source                                    |
| --------------------------------------- | -------------------------------- | ----------------------------------------- |
| Queue name                              | `lolday-u-<12-hex-of-user-uuid>` | spec §6.3                                 |
| weight                                  | 1                                | DRF: equal share between users            |
| reclaimable                             | true                             | spec §6.3                                 |
| capability.cpu                          | "8"                              | matches lolday-jobs-quota.requests.cpu    |
| capability.memory                       | 30Gi                             | matches lolday-jobs-quota.requests.memory |
| capability.nvidia.com/gpu               | "2"                              | cluster total — see OQ-1 above            |
| `lolday-training` (fallback) capability | same                             | safety net for non-user code paths        |

---

## File map

**Modified:**

- `charts/lolday/templates/volcano-queue.yaml` — add `capability` block to `lolday-training`.
- `backend/app/services/k8s.py` — add `VOLCANO_SCHED_GROUP`, `VOLCANO_QUEUE_PLURAL` constants + `queue_name_for_user()` + `ensure_user_queue()`.
- `backend/app/services/job_spec.py::build_volcano_job_manifest` — accept new keyword arg `queue_name: str` (no default — caller must specify).
- `backend/app/routers/jobs.py::create_job` — `queue_name = ensure_user_queue(user.id)` before `build_volcano_job_manifest(...)`.
- `docs/architecture.md` §10 — add a gotcha about per-user queues.
- `.claude/rules/charts-and-helm.md` — note the queue lifecycle (created lazily, not in chart).

**New:**

- `backend/tests/test_services_k8s_user_queue.py` — unit tests for `queue_name_for_user` + `ensure_user_queue` (idempotency + 409 handling).
- `tests/2026-05-05-phase2-fair-share-smoke.sh` — verifies queue capability + scheduler plugins (post-deploy check; optional dry-run because no live job is needed).

**Not touched:** scheduler config (already correct), GPU profile enum (Phase 3), alerts (Phase 4 already shipped).

---

## Execution order

```
Wave 0 (parallel — independent file work)
├── Task 1: branch confirmed
├── Task 2: volcano-queue.yaml — add capability
├── Task 3: services/k8s.py — queue helpers
├── Task 4: services/job_spec.py — accept queue_name
└── Task 5: routers/jobs.py — call ensure_user_queue

Wave 1 (sequential — tests + verification)
├── Task 6: backend unit tests
├── Task 7: helm template + helm lint
├── Task 8: pytest backend
└── Task 9: smoke test script

Wave 2 (sequential — close out)
├── Task 10: docs (architecture.md, charts-and-helm.md)
├── Task 11: pre-commit + commit + push + PR
└── Task 12: deploy.sh + post-deploy smoke (operator-attended; deferred until Phase 5 backend image rebuild is bundled together)
```

---

## Task 2: `charts/lolday/templates/volcano-queue.yaml` — add capability

Find `spec:` block, replace:

```yaml
spec:
  weight: 1
  reclaimable: true
```

With:

```yaml
spec:
  weight: 1
  reclaimable: true
  # Phase 2 — fallback queue for non-user / pre-cutover code paths.
  # Per-user queues created lazily by services/k8s.ensure_user_queue
  # carry the same cap. Numbers from spec §7 Phase 2.
  capability:
    cpu: "8"
    memory: 30Gi
    nvidia.com/gpu: "2"
```

---

## Task 3: `backend/app/services/k8s.py` — queue helpers

Append at end of file:

```python
import logging
import uuid as _uuid

from kubernetes.client.exceptions import ApiException

logger = logging.getLogger(__name__)

# Phase 2 — Volcano scheduling group (distinct from the batch group used for vcjob).
VOLCANO_SCHED_GROUP = "scheduling.volcano.sh"
VOLCANO_SCHED_VERSION = "v1beta1"
VOLCANO_QUEUE_PLURAL = "queues"

# Per-user queue capability — matches the lolday-jobs-quota at the namespace
# level, so a single user can never exceed the cluster's workload allowance.
# DRF + proportion (already enabled by the Volcano sub-chart) handle
# fair-share between queues. Spec §6.3 OQ-1: set gpu=2 (sum cap) so a
# single-user GPU2 job is allowed but two GPU2 jobs from the same user are not.
_USER_QUEUE_CAPABILITY = {
    "cpu": "8",
    "memory": "30Gi",
    "nvidia.com/gpu": "2",
}


def queue_name_for_user(user_id: _uuid.UUID) -> str:
    """Stable per-user Volcano queue name.

    12-hex prefix is enough to avoid collisions for the foreseeable user count
    (16^12 = 2.8e14 names) and keeps the queue name within DNS-1123 (63 chars).
    """
    return f"lolday-u-{user_id.hex[:12]}"


def ensure_user_queue(user_id: _uuid.UUID) -> str:
    """Idempotently create a per-user Volcano Queue. Returns the queue name.

    Volcano Queue is cluster-scoped. K8s 409 (AlreadyExists) is treated as
    success — the queue may have been created by a previous request from
    the same user, or a parallel request racing this one. Any other ApiException
    propagates so the caller (routers/jobs.create_job) can return 5xx instead
    of silently submitting a job that has no queue.
    """
    name = queue_name_for_user(user_id)
    body = {
        "apiVersion": f"{VOLCANO_SCHED_GROUP}/{VOLCANO_SCHED_VERSION}",
        "kind": "Queue",
        "metadata": {
            "name": name,
            "labels": {
                "lolday.io/role": "user-queue",
                "lolday.io/user-id": str(user_id),
            },
        },
        "spec": {
            "weight": 1,
            "reclaimable": True,
            "capability": _USER_QUEUE_CAPABILITY,
        },
    }
    try:
        volcano_v1alpha1().create_cluster_custom_object(
            group=VOLCANO_SCHED_GROUP,
            version=VOLCANO_SCHED_VERSION,
            plural=VOLCANO_QUEUE_PLURAL,
            body=body,
        )
        logger.info("created user queue %s", name)
    except ApiException as e:
        if e.status != 409:
            raise
        # 409 is the idempotent path; nothing to log loudly.
    return name
```

> Note: `volcano_v1alpha1()` already returns a `CustomObjectsApi` — the function name is historical (it was originally created for the batch group's v1alpha1, but the API client is generic). Reuse instead of introducing a parallel `volcano_v1beta1()` whose body would be identical.

---

## Task 4: `backend/app/services/job_spec.py` — accept `queue_name`

Find the signature:

```python
def build_volcano_job_manifest(
    *,
    job_id: uuid.UUID,
    job_type: JobType,
    ...
    resource_profile: ResourceProfile = ResourceProfile.STANDARD,
    gpu_strategy: str = "ddp",
) -> dict[str, Any]:
```

Add `queue_name: str` (no default — caller must pass):

```python
def build_volcano_job_manifest(
    *,
    job_id: uuid.UUID,
    job_type: JobType,
    ...
    resource_profile: ResourceProfile = ResourceProfile.STANDARD,
    gpu_strategy: str = "ddp",
    queue_name: str,
) -> dict[str, Any]:
```

In the returned manifest body, replace:

```yaml
            "queue": "lolday-training",
```

With:

```python
            "queue": queue_name,
```

---

## Task 5: `backend/app/routers/jobs.py::create_job` — call `ensure_user_queue`

Find the import line:

```python
from app.services.k8s import core_v1
```

Replace with:

```python
from app.services.k8s import core_v1, ensure_user_queue
```

(This import is around line 30-40 — adapt to actual current state.)

Right before the `manifest = build_volcano_job_manifest(...)` call (around line 347):

```python
    queue_name = ensure_user_queue(user.id)
    manifest = build_volcano_job_manifest(
        ...
        queue_name=queue_name,
    )
```

---

## Task 6: `backend/tests/test_services_k8s_user_queue.py` — unit tests

```python
"""Phase 2 — per-user Volcano queue helpers."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException

from app.services.k8s import ensure_user_queue, queue_name_for_user


def test_queue_name_for_user_format() -> None:
    uid = uuid.UUID("ab12cd34ef567890abcdef0123456789")
    assert queue_name_for_user(uid) == "lolday-u-ab12cd34ef56"


def test_queue_name_for_user_distinct() -> None:
    a = uuid.uuid4()
    b = uuid.uuid4()
    assert queue_name_for_user(a) != queue_name_for_user(b)


def test_ensure_user_queue_creates_and_returns_name() -> None:
    uid = uuid.uuid4()
    fake_api = MagicMock()
    fake_api.create_cluster_custom_object = MagicMock()
    with patch("app.services.k8s.volcano_v1alpha1", return_value=fake_api):
        name = ensure_user_queue(uid)
    assert name == queue_name_for_user(uid)
    fake_api.create_cluster_custom_object.assert_called_once()
    body = fake_api.create_cluster_custom_object.call_args.kwargs["body"]
    assert body["kind"] == "Queue"
    assert body["spec"]["capability"]["nvidia.com/gpu"] == "2"
    assert body["metadata"]["labels"]["lolday.io/user-id"] == str(uid)


def test_ensure_user_queue_409_is_idempotent() -> None:
    uid = uuid.uuid4()
    fake_api = MagicMock()
    fake_api.create_cluster_custom_object.side_effect = ApiException(status=409)
    with patch("app.services.k8s.volcano_v1alpha1", return_value=fake_api):
        # must not raise
        name = ensure_user_queue(uid)
    assert name == queue_name_for_user(uid)


def test_ensure_user_queue_other_error_propagates() -> None:
    uid = uuid.uuid4()
    fake_api = MagicMock()
    fake_api.create_cluster_custom_object.side_effect = ApiException(status=500)
    with patch("app.services.k8s.volcano_v1alpha1", return_value=fake_api):
        with pytest.raises(ApiException):
            ensure_user_queue(uid)
```

---

## Task 7: helm template + helm lint

```bash
helm lint charts/lolday
helm template charts/lolday --set monitoring.grafana.adminPassword=x --set mlflow.db.password=x --set backend.harborAdminPassword=x --set cloudflare.tunnelToken=x --set backend.fernetKey=x --set monitoring.postgresExporter.password=x --show-only templates/volcano-queue.yaml
```

Expected: queue spec contains `capability.cpu: "8"`, etc.

---

## Task 8: pytest backend

```bash
cd backend && uv run pytest tests/test_services_k8s_user_queue.py -v
cd backend && uv run pytest tests/test_services_job_spec.py -v
cd backend && uv run pytest tests/test_routers_jobs.py -v 2>&1 | tail -20  # may have pre-existing skips
```

---

## Task 9: smoke test

`tests/2026-05-05-phase2-fair-share-smoke.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
fail=0

echo "[step 1/3] lolday-training queue has capability cap"
cap=$(kubectl get queue.scheduling.volcano.sh lolday-training -o jsonpath='{.spec.capability}' 2>/dev/null)
if [ -n "${cap}" ]; then
  echo "OK: ${cap}"
else
  echo "FAIL: lolday-training has no capability"
  fail=1
fi

echo ""
echo "[step 2/3] scheduler config has drf + proportion plugins"
plugins=$(kubectl -n lolday get cm lolday-scheduler-configmap -o jsonpath='{.data.volcano-scheduler\.conf}' 2>/dev/null)
if echo "${plugins}" | grep -q "name: drf" && echo "${plugins}" | grep -q "name: proportion"; then
  echo "OK"
else
  echo "FAIL: drf or proportion plugin missing"
  fail=1
fi

echo ""
echo "[step 3/3] backend SA can create cluster-scoped Queues"
out=$(kubectl auth can-i create queues.scheduling.volcano.sh \
  --as=system:serviceaccount:lolday:backend 2>&1 || true)
case "${out}" in
  yes) echo "OK" ;;
  *) echo "WARN: backend SA cannot create cluster-scoped Queues (${out}); ensure_user_queue will fail unless ClusterRole grants this — check backend-rbac.yaml" ; fail=1 ;;
esac

echo ""
[ "${fail}" -eq 0 ] && echo "=== SMOKE PASSED ===" || { echo "=== SMOKE FAILED ==="; exit 1; }
```

> Step 3 may surface missing RBAC: backend SA's existing ClusterRole only grants `nodes` (Phase 7.5). Creating cluster-scoped Queues needs `scheduling.volcano.sh/queues create`. Plan adds this to the existing ClusterRole (see Task 5 follow-up).

---

## Task 5b — RBAC update (discovered during plan review)

`charts/lolday/templates/backend-rbac.yaml` ClusterRole `<ns>-backend-cluster-reader`:

```yaml
rules:
  - apiGroups: [""]
    resources: [nodes]
    verbs: [get, list, watch]
  # Phase 2 — per-user Volcano Queue (cluster-scoped) creation.
  - apiGroups: [scheduling.volcano.sh]
    resources: [queues]
    verbs: [get, list, create]
```

Rename ClusterRole? Existing name `<ns>-backend-cluster-reader` no longer accurate (now creates queues). Acceptable: rename to `<ns>-backend-cluster-ops` in same PR. The corresponding ClusterRoleBinding `roleRef.name` must follow.

---

## Task 11: pre-commit + commit + push + PR

Standard flow. PR title: `feat(backend, charts): phase 2 — Volcano per-user queue + capability cap`.

---

## Task 12: deploy (operator-attended, deferred)

> **Note:** Phase 2 backend code changes only ship after a backend image rebuild. Deferred until Phase 3 + 5 are also coded — single image rebuild + values.yaml bump covers all four (P2/P3/P4-gauge/P5).
>
> Helm side (`volcano-queue.yaml` capability + RBAC ClusterRole change) ships with the next `bash scripts/deploy.sh` independently of the image bump.

---

## Self-review checklist

- [ ] capability values in chart + Python both match spec §7 Phase 2.
- [ ] `queue_name_for_user` uses 12-hex prefix (DNS-1123 safe, >2e14 unique).
- [ ] `ensure_user_queue` 409 is silently OK; non-409 raises.
- [ ] backend ClusterRole grants `queues create` (Task 5b).
- [ ] All 5 unit tests cover happy path + idempotency + error propagation.
- [ ] No `await` on sync K8s client calls.
- [ ] Deploy of just the helm side does NOT break existing flows (per-user queue not yet referenced; jobs continue going to `lolday-training` until backend image rebuilds).
