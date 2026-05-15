"""M-token-secret-owner: dispatch_job_to_volcano patches the job-token Secret
with ownerReferences pointing at the just-created vcjob, so K8s GC cascades
the Secret deletion when the vcjob is removed."""

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest


@pytest.mark.asyncio
async def test_dispatch_patches_token_secret_with_vcjob_owner(
    db_session, seed_user, seed_detector_version
):
    from app.models.job import Job, JobStatus, JobType
    from app.services.job_dispatch import dispatch_job_to_volcano

    # Stub state we can inspect later.
    patches: list = []
    created_vcjob_uid = "vcjob-uid-deadbeef"

    class _Volcano:
        def create_namespaced_custom_object(
            self, group, version, namespace, plural, body, **kw
        ):
            body.setdefault("metadata", {}).setdefault("uid", created_vcjob_uid)
            return body

    class _Core:
        def create_namespaced_secret(self, namespace, body, **kw):
            return body

        def patch_namespaced_secret(self, name, namespace, body, **kw):
            patches.append((name, namespace, body))
            return body

    # ensure_user_queue is async; stub it to a literal queue name.
    async def _fake_queue(_owner_id):
        return "lolday-u-fake"

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
        patch("app.services.job_dispatch.volcano_v1alpha1", return_value=_Volcano()),
        patch("app.services.job_dispatch.core_v1", return_value=_Core()),
        patch("app.services.job_dispatch.ensure_user_queue", _fake_queue),
    ):
        await dispatch_job_to_volcano(db_session, job)

    assert len(patches) == 1
    name, namespace, body = patches[0]
    assert name.startswith("job-token-")
    assert namespace  # lolday or lolday-jobs depending on settings
    owner_refs = body["metadata"]["ownerReferences"]
    assert len(owner_refs) == 1
    assert owner_refs[0]["kind"] == "Job"  # Volcano Job (not batch/v1)
    assert owner_refs[0]["apiVersion"].startswith("batch.volcano.sh/")
    assert owner_refs[0]["uid"] == created_vcjob_uid
    assert owner_refs[0]["blockOwnerDeletion"] is False
    assert owner_refs[0]["controller"] is False
