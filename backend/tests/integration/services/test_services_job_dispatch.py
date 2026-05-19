"""Coverage for the four ``dispatch_job_to_volcano`` branches not exercised
by ``tests/integration/routers/test_jobs_dispatch_owner_ref.py`` (happy
path) or ``tests/integration/reconciler/test_fifo_scheduler.py`` (caller
contract):

- DetectorVersion FK invariant violated -> ``RuntimeError``.
- ``Job.source_model_version_id`` set -> ModelVersion resolved, run id
  threaded into ``build_volcano_job_manifest`` as ``source_run_id``.
- vcjob create raises -> token Secret rollback path, exception propagates.
- vcjob create returns metadata without ``uid`` -> ownerRef patch skipped.
- ownerRef patch raises -> swallowed,
  ``BACKEND_ERRORS{stage="token_secret_owner_patch"}`` incremented.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest


def _make_volcano_stub(uid: str | None = "vcjob-uid-deadbeef"):
    class _Volcano:
        def create_namespaced_custom_object(
            self, group, version, namespace, plural, body, **kw
        ):
            md = body.setdefault("metadata", {})
            if uid is not None:
                md.setdefault("uid", uid)
            return body

    return _Volcano()


def _make_core_stub(
    *,
    deleted: list | None = None,
    patches: list | None = None,
    patch_raises: BaseException | None = None,
):
    class _Core:
        def create_namespaced_secret(self, namespace, body, **kw):
            return body

        def delete_namespaced_secret(self, name, namespace, **kw):
            if deleted is not None:
                deleted.append((name, namespace))

        def patch_namespaced_secret(self, name, namespace, body, **kw):
            if patch_raises is not None:
                raise patch_raises
            if patches is not None:
                patches.append((name, namespace, body))
            return body

    return _Core()


async def _fake_queue(_owner_id):
    return "lolday-u-fake"


def _backend_errors_stage(stage: str) -> float:
    from app.metrics import BACKEND_ERRORS

    return BACKEND_ERRORS.labels(stage=stage)._value.get()


@pytest.mark.asyncio
async def test_dispatch_raises_on_missing_detector_version(db_session, seed_user):
    """Job referencing a non-existent DetectorVersion -> RuntimeError.

    Guards the FK-invariant message so a future ORM-cascade change surfaces
    as a clear runtime error rather than a 500 deep inside the manifest
    builder.
    """
    from app.models.job import Job, JobStatus, JobType
    from app.services.job_dispatch import dispatch_job_to_volcano

    bogus_dv_id = uuid4()
    job = Job(
        type=JobType.TRAIN,
        status=JobStatus.QUEUED_BACKEND,
        detector_version_id=bogus_dv_id,
        owner_id=seed_user.id,
        resolved_config={},
        idempotency_key=uuid4().hex,
    )
    # Bypass FK at insert time so we can reach the dispatch-side check.
    # aiosqlite enforces FKs at commit; using session.add+flush would fail
    # for a different reason. Instead, exercise dispatch directly without
    # committing the row.
    with pytest.raises(RuntimeError, match="FK invariant violated"):
        await dispatch_job_to_volcano(db_session, job)


@pytest.mark.asyncio
async def test_dispatch_threads_source_model_run_id_into_manifest(
    db_session, seed_user, seed_model_version
):
    """Predict-style job with ``source_model_version_id`` resolves the
    ModelVersion and forwards ``source_run_id`` + the
    ``runs:/<run>/model`` artifact path into ``build_volcano_job_manifest``.
    """
    from app.models import ModelVersion, RegisteredModel
    from app.models.job import Job, JobStatus, JobType
    from app.services.job_dispatch import dispatch_job_to_volcano
    from sqlalchemy import select

    _name, _version = await seed_model_version()
    mv_row = (
        await db_session.execute(
            select(ModelVersion)
            .join(
                RegisteredModel, ModelVersion.registered_model_id == RegisteredModel.id
            )
            .where(ModelVersion.owner_id == seed_user.id)
            .order_by(ModelVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    assert mv_row.mlflow_run_id is not None

    job = Job(
        type=JobType.PREDICT,
        status=JobStatus.QUEUED_BACKEND,
        detector_version_id=mv_row.detector_version_id,
        source_model_version_id=mv_row.id,
        owner_id=seed_user.id,
        resolved_config={},
        idempotency_key=uuid4().hex,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    seen: dict = {}

    def fake_build(**kwargs):
        seen.update(kwargs)
        return {"metadata": {"name": f"job-{job.id.hex}"}, "spec": {}}

    with (
        patch(
            "app.services.job_dispatch.volcano_v1alpha1",
            return_value=_make_volcano_stub(),
        ),
        patch("app.services.job_dispatch.core_v1", return_value=_make_core_stub()),
        patch("app.services.job_dispatch.ensure_user_queue", _fake_queue),
        patch(
            "app.services.job_dispatch.build_volcano_job_manifest",
            side_effect=fake_build,
        ),
    ):
        await dispatch_job_to_volcano(db_session, job)

    assert seen["source_run_id"] == mv_row.mlflow_run_id
    assert seen["source_artifact_path"] is not None
    assert (
        seen["source_artifact_path"].endswith("/model")
        or "model" in seen["source_artifact_path"]
    )


@pytest.mark.asyncio
async def test_dispatch_rolls_back_token_secret_on_vcjob_create_failure(
    db_session, seed_user, seed_detector_version
):
    """vcjob create raising must (1) delete the just-created token Secret
    and (2) re-raise so the caller can roll back the DB session.

    Validates the partial-failure cleanup contract documented in the
    ``Idempotency notes`` block at the top of ``job_dispatch.py``.
    """
    from app.models.job import Job, JobStatus, JobType
    from app.services.job_dispatch import dispatch_job_to_volcano

    deleted: list = []

    class _FailingVolcano:
        def create_namespaced_custom_object(self, *a, **kw):
            raise RuntimeError("k8s API unavailable")

    dv_id = await seed_detector_version()
    job = Job(
        type=JobType.TRAIN,
        status=JobStatus.QUEUED_BACKEND,
        detector_version_id=UUID(dv_id),
        owner_id=seed_user.id,
        resolved_config={},
        idempotency_key=uuid4().hex,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    original_status = job.status

    with (
        patch(
            "app.services.job_dispatch.volcano_v1alpha1", return_value=_FailingVolcano()
        ),
        patch(
            "app.services.job_dispatch.core_v1",
            return_value=_make_core_stub(deleted=deleted),
        ),
        patch("app.services.job_dispatch.ensure_user_queue", _fake_queue),
        pytest.raises(RuntimeError, match="k8s API unavailable"),
    ):
        await dispatch_job_to_volcano(db_session, job)

    assert len(deleted) == 1, "rollback path did not delete the token Secret"
    name, namespace = deleted[0]
    assert name.startswith("job-token-")
    assert namespace
    assert job.status == original_status, "job status must not advance on failure"
    assert job.k8s_job_name is None


@pytest.mark.asyncio
async def test_dispatch_skips_owner_ref_when_vcjob_uid_missing(
    db_session, seed_user, seed_detector_version, caplog
):
    """vcjob response without ``metadata.uid`` -> ownerRef patch skipped,
    warning logged, job still advances to PREPARING.
    """
    import logging

    from app.models.job import Job, JobStatus, JobType
    from app.services.job_dispatch import dispatch_job_to_volcano

    patches: list = []

    dv_id = await seed_detector_version()
    job = Job(
        type=JobType.TRAIN,
        status=JobStatus.QUEUED_BACKEND,
        detector_version_id=UUID(dv_id),
        owner_id=seed_user.id,
        resolved_config={},
        idempotency_key=uuid4().hex,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with (
        caplog.at_level(logging.WARNING, logger="app.services.job_dispatch"),
        patch(
            "app.services.job_dispatch.volcano_v1alpha1",
            return_value=_make_volcano_stub(uid=None),
        ),
        patch(
            "app.services.job_dispatch.core_v1",
            return_value=_make_core_stub(patches=patches),
        ),
        patch("app.services.job_dispatch.ensure_user_queue", _fake_queue),
    ):
        await dispatch_job_to_volcano(db_session, job)

    assert patches == [], "ownerRef patch must be skipped when uid is missing"
    assert any("missing metadata.uid" in rec.message for rec in caplog.records), (
        "expected warning about missing uid"
    )
    assert job.status == JobStatus.PREPARING
    assert job.k8s_job_name is not None


@pytest.mark.asyncio
async def test_dispatch_swallows_owner_ref_patch_failure(
    db_session, seed_user, seed_detector_version, caplog
):
    """ownerRef patch raising must (1) be swallowed (job still advances to
    PREPARING) and (2) increment ``BACKEND_ERRORS{stage=token_secret_owner_patch}``.
    """
    import logging

    from app.models.job import Job, JobStatus, JobType
    from app.services.job_dispatch import dispatch_job_to_volcano

    before = _backend_errors_stage("token_secret_owner_patch")

    dv_id = await seed_detector_version()
    job = Job(
        type=JobType.TRAIN,
        status=JobStatus.QUEUED_BACKEND,
        detector_version_id=UUID(dv_id),
        owner_id=seed_user.id,
        resolved_config={},
        idempotency_key=uuid4().hex,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with (
        caplog.at_level(logging.WARNING, logger="app.services.job_dispatch"),
        patch(
            "app.services.job_dispatch.volcano_v1alpha1",
            return_value=_make_volcano_stub(),
        ),
        patch(
            "app.services.job_dispatch.core_v1",
            return_value=_make_core_stub(patch_raises=RuntimeError("conflict")),
        ),
        patch("app.services.job_dispatch.ensure_user_queue", _fake_queue),
    ):
        await dispatch_job_to_volcano(db_session, job)

    after = _backend_errors_stage("token_secret_owner_patch")
    assert after == before + 1, (
        "BACKEND_ERRORS{stage=token_secret_owner_patch} must inc"
    )
    assert any("ownerRef patch failed" in rec.message for rec in caplog.records), (
        "expected warning about ownerRef patch failure"
    )
    assert job.status == JobStatus.PREPARING
    assert job.k8s_job_name is not None
