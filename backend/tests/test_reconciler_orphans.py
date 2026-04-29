"""Tests for orphan vcjob reconciliation.

Covers the case where a Volcano Job exists in K8s but the corresponding
`job` row is missing from the DB. The reconciler lists vcjobs, cross-
checks the `lolday.job-id` label against the DB, and deletes orphans
(with their associated job-token Secret).
"""

import uuid
from datetime import UTC
from unittest.mock import patch

import pytest
from app.models.job import Job, JobStatus, JobType
from app.reconciler import reconcile_orphan_vcjobs


@pytest.fixture
async def seed_job(db_session, seed_detector_version, seed_dataset, seed_user):
    """Insert a Job row with all required FKs and return it."""

    async def _seed(
        status: JobStatus = JobStatus.RUNNING,
        job_type: JobType = JobType.TRAIN,
    ) -> Job:
        dv_id = await seed_detector_version(name=f"det-{uuid.uuid4().hex[:6]}")
        tr = await seed_dataset(name=f"ds-{uuid.uuid4().hex[:6]}")
        te = await seed_dataset(name=f"ds-{uuid.uuid4().hex[:6]}")
        j = Job(
            type=job_type,
            status=status,
            detector_version_id=uuid.UUID(dv_id),
            train_dataset_id=uuid.UUID(tr),
            test_dataset_id=uuid.UUID(te),
            owner_id=seed_user.id,
            resolved_config={},
            mlflow_experiment_id="42",
            mlflow_run_id=f"run-{uuid.uuid4().hex[:8]}",
            idempotency_key=uuid.uuid4().hex,
            token_hash="a" * 64,
            k8s_job_name=f"job-{job_type.value}-{uuid.uuid4().hex[:8]}",
        )
        db_session.add(j)
        await db_session.commit()
        await db_session.refresh(j)
        return j

    return _seed


def _vcjob(name: str, job_id: str | None) -> dict:
    """Build a minimal vcjob dict with a `lolday.job-id` label.

    Mirrors the structure produced by app.services.job_spec — the label
    lives on the first task's pod template.
    """
    labels: dict[str, str] = {}
    if job_id is not None:
        labels["lolday.job-id"] = job_id
    return {
        "metadata": {"name": name},
        "spec": {
            "tasks": [
                {"template": {"metadata": {"labels": labels}}},
            ],
        },
    }


@pytest.mark.asyncio
async def test_orphan_vcjob_is_deleted(db_session, seed_job):
    """A vcjob whose lolday.job-id is NOT in DB should be deleted."""
    matched_job = await seed_job(status=JobStatus.RUNNING, job_type=JobType.TRAIN)
    orphan_uuid = str(uuid.uuid4())

    delete_calls: list[str] = []
    secret_delete_calls: list[str] = []

    class _VolcanoStub:
        def list_namespaced_custom_object(self, *a, **kw):
            return {
                "items": [
                    _vcjob("job-train-matched", str(matched_job.id)),
                    _vcjob("job-train-orphan", orphan_uuid),
                ]
            }

        def delete_namespaced_custom_object(self, *a, **kw):
            delete_calls.append(kw["name"])

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):
            secret_delete_calls.append(kw["name"])

    with patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()):
        with patch("app.reconciler.core_v1", return_value=_CoreStub()):
            await reconcile_orphan_vcjobs(db_session)

    assert delete_calls == ["job-train-orphan"], delete_calls
    # secret name is derived from the orphan UUID's first 16 hex chars (no dashes)
    expected_secret = f"job-token-{orphan_uuid.replace('-', '')[:16]}"
    assert secret_delete_calls == [expected_secret], secret_delete_calls


@pytest.mark.asyncio
async def test_matched_vcjob_is_left_alone(db_session, seed_job):
    """A vcjob whose lolday.job-id matches a DB row must NOT be deleted."""
    matched_job = await seed_job(status=JobStatus.RUNNING, job_type=JobType.TRAIN)

    delete_calls: list[str] = []

    class _VolcanoStub:
        def list_namespaced_custom_object(self, *a, **kw):
            return {"items": [_vcjob("job-train-matched", str(matched_job.id))]}

        def delete_namespaced_custom_object(self, *a, **kw):
            delete_calls.append(kw["name"])

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):
            pass

    with patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()):
        with patch("app.reconciler.core_v1", return_value=_CoreStub()):
            await reconcile_orphan_vcjobs(db_session)

    assert delete_calls == [], delete_calls


@pytest.mark.asyncio
async def test_unlabeled_vcjob_is_skipped(db_session):
    """A vcjob with no `lolday.job-id` label is foreign — never delete."""
    delete_calls: list[str] = []

    class _VolcanoStub:
        def list_namespaced_custom_object(self, *a, **kw):
            return {"items": [_vcjob("foreign-vcjob", None)]}

        def delete_namespaced_custom_object(self, *a, **kw):
            delete_calls.append(kw["name"])

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):
            pass

    with patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()):
        with patch("app.reconciler.core_v1", return_value=_CoreStub()):
            await reconcile_orphan_vcjobs(db_session)

    assert delete_calls == [], delete_calls


@pytest.mark.asyncio
async def test_secret_404_is_tolerated(db_session, seed_job):
    """Missing job-token Secret (already cleaned up) must not raise."""
    from kubernetes.client import ApiException

    orphan_uuid = str(uuid.uuid4())
    delete_calls: list[str] = []

    class _VolcanoStub:
        def list_namespaced_custom_object(self, *a, **kw):
            return {"items": [_vcjob("job-train-orphan", orphan_uuid)]}

        def delete_namespaced_custom_object(self, *a, **kw):
            delete_calls.append(kw["name"])

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):
            raise ApiException(status=404)

    with patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()):
        with patch("app.reconciler.core_v1", return_value=_CoreStub()):
            await reconcile_orphan_vcjobs(db_session)

    assert delete_calls == ["job-train-orphan"], delete_calls


@pytest.mark.asyncio
async def test_list_apiexception_propagates(db_session):
    """A failed Volcano API list must surface as an exception, so
    `reconciler_loop` logs + counts it like the other reconcile passes
    (regression guard against silently returning 0)."""
    from kubernetes.client import ApiException

    class _VolcanoStub:
        def list_namespaced_custom_object(self, *a, **kw):
            raise ApiException(status=403, reason="Forbidden")

        def delete_namespaced_custom_object(self, *a, **kw):  # never reached
            raise AssertionError("delete must not run when list failed")

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):  # never reached
            raise AssertionError("secret delete must not run when list failed")

    with patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()):
        with patch("app.reconciler.core_v1", return_value=_CoreStub()):
            with pytest.raises(ApiException):
                await reconcile_orphan_vcjobs(db_session)


@pytest.mark.asyncio
async def test_delete_non_404_apiexception_continues(db_session):
    """A 5xx on one delete must not abort the iteration — the reconciler
    moves on to the next vcjob."""
    from kubernetes.client import ApiException

    orphan_a = str(uuid.uuid4())
    orphan_b = str(uuid.uuid4())
    delete_attempts: list[str] = []
    secret_attempts: list[str] = []

    class _VolcanoStub:
        def list_namespaced_custom_object(self, *a, **kw):
            return {
                "items": [
                    _vcjob("job-train-a", orphan_a),
                    _vcjob("job-train-b", orphan_b),
                ]
            }

        def delete_namespaced_custom_object(self, *a, **kw):
            delete_attempts.append(kw["name"])
            if kw["name"] == "job-train-a":
                raise ApiException(status=500, reason="server error")

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):
            secret_attempts.append(kw["name"])

    with patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()):
        with patch("app.reconciler.core_v1", return_value=_CoreStub()):
            await reconcile_orphan_vcjobs(db_session)

    # both vcjobs were attempted; only the second succeeded so its secret
    # cleanup ran. The first is left for the next pass.
    assert delete_attempts == ["job-train-a", "job-train-b"], delete_attempts
    assert secret_attempts == [f"job-token-{orphan_b.replace('-', '')[:16]}"], (
        secret_attempts
    )


@pytest.mark.asyncio
async def test_vcjob_404_still_cleans_secret(db_session):
    """If the vcjob is already gone (404) but the orphan token Secret
    survives, secret cleanup must still run — otherwise stale Secrets
    accumulate forever."""
    from kubernetes.client import ApiException

    orphan_uuid = str(uuid.uuid4())
    secret_attempts: list[str] = []

    class _VolcanoStub:
        def list_namespaced_custom_object(self, *a, **kw):
            return {"items": [_vcjob("job-train-orphan", orphan_uuid)]}

        def delete_namespaced_custom_object(self, *a, **kw):
            raise ApiException(status=404)

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):
            secret_attempts.append(kw["name"])

    with patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()):
        with patch("app.reconciler.core_v1", return_value=_CoreStub()):
            await reconcile_orphan_vcjobs(db_session)

    assert secret_attempts == [f"job-token-{orphan_uuid.replace('-', '')[:16]}"], (
        secret_attempts
    )


@pytest.mark.asyncio
async def test_age_guard_skips_freshly_created_vcjobs(db_session):
    """A vcjob younger than ORPHAN_GRACE_SECONDS must NOT be deleted —
    that window covers the gap between the API's K8s create and DB
    commit, so a freshly-submitted job isn't ripped out from under
    the user."""
    from datetime import datetime

    fresh_uuid = str(uuid.uuid4())
    fresh_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    delete_calls: list[str] = []

    fresh_vjob = _vcjob("job-train-fresh", fresh_uuid)
    fresh_vjob["metadata"]["creationTimestamp"] = fresh_ts

    class _VolcanoStub:
        def list_namespaced_custom_object(self, *a, **kw):
            return {"items": [fresh_vjob]}

        def delete_namespaced_custom_object(self, *a, **kw):
            delete_calls.append(kw["name"])

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):
            pass

    with patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()):
        with patch("app.reconciler.core_v1", return_value=_CoreStub()):
            await reconcile_orphan_vcjobs(db_session)

    assert delete_calls == [], delete_calls


@pytest.mark.asyncio
async def test_malformed_label_increments_metric(db_session):
    """A vcjob carrying a non-UUID lolday.job-id label is foreign data —
    we skip it AND increment a metric so the dashboard surfaces the
    bad emitter."""
    from app.metrics import BACKEND_ERRORS

    counter = BACKEND_ERRORS.labels(stage="orphan_vcjob_malformed_label")
    before = counter._value.get() if hasattr(counter, "_value") else 0

    bad = _vcjob("job-train-bad", "not-a-uuid-at-all")

    delete_calls: list[str] = []

    class _VolcanoStub:
        def list_namespaced_custom_object(self, *a, **kw):
            return {"items": [bad]}

        def delete_namespaced_custom_object(self, *a, **kw):
            delete_calls.append(kw["name"])

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):
            pass

    with patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()):
        with patch("app.reconciler.core_v1", return_value=_CoreStub()):
            await reconcile_orphan_vcjobs(db_session)

    assert delete_calls == [], delete_calls
    after = counter._value.get() if hasattr(counter, "_value") else 0
    assert after == before + 1, (before, after)
