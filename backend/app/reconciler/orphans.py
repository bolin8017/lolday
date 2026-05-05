"""Orphan Volcano-job cleanup.

A schema migration / DB rebuild can leave Volcano Jobs in K8s that the
backend no longer knows about; their init containers crash on every pod
with "job not found" and KubeContainerWaiting fires forever. This module
runs a periodic scan from :func:`reconciler_loop`, lists vcjobs in the
job namespace, cross-checks each ``lolday.job-id`` label against the DB,
and deletes orphans (with their associated job-token Secret).

The :data:`ORPHAN_GRACE_SECONDS` guard skips vcjobs younger than 5 min
to avoid the create-vcjob/commit-row race in ``app/routers/jobs.py``.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from kubernetes.client import ApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    core_v1,
    volcano_v1alpha1,
)

logger = logging.getLogger(__name__)

ORPHAN_GRACE_SECONDS = 300  # don't touch a vcjob younger than this — see below.


async def reconcile_orphan_vcjobs(session: AsyncSession) -> int:
    """Delete Volcano Jobs whose ``lolday.job-id`` label has no matching DB row.

    A schema migration / DB rebuild can leave Volcano Jobs in K8s that the
    backend no longer knows about. Their init container then dies on every
    pod with "job not found", the pod stays Init:Error indefinitely, and
    KubeContainerWaiting fires forever. This pass closes that loop.

    Race-window guard: ``app.routers.jobs`` flushes the Job DB row, calls
    ``volcano_v1alpha1().create_namespaced_custom_object()``, then commits.
    A reconciler running with an independent session at PostgreSQL
    READ COMMITTED would not see the uncommitted row and could delete
    the freshly-created vcjob. Skipping vcjobs younger than
    ``ORPHAN_GRACE_SECONDS`` is enough headroom for the API request to
    finish committing, and the next pass picks up genuinely-orphaned ones.

    Listing failures bubble up — the surrounding ``reconciler_loop`` already
    logs + counts iteration failures consistently with reconcile_build /
    reconcile_job / sync_model_versions.

    Returns the number of orphans deleted, for metrics.
    """
    from app.services.job_spec import _job_token_secret_name

    listing = await asyncio.to_thread(
        volcano_v1alpha1().list_namespaced_custom_object,
        group=VOLCANO_BATCH_GROUP,
        version=VOLCANO_BATCH_VERSION,
        namespace=settings.JOB_NAMESPACE,
        plural=VOLCANO_JOB_PLURAL,
    )

    now = datetime.now(UTC)
    deleted = 0
    for vjob in listing.get("items", []):
        meta = vjob.get("metadata", {}) or {}
        name = meta.get("name", "")
        # Volcano stamps the same labels both at the job level and on the
        # task pod template — read the top-level copy first (survives task
        # restructuring), with the deeper path as a fallback for older
        # vcjobs / chart variants that only set it on the pod template.
        label = (meta.get("labels") or {}).get("lolday.job-id")
        if not label:
            tasks = vjob.get("spec", {}).get("tasks") or []
            if tasks:
                label = (
                    (tasks[0].get("template") or {})
                    .get("metadata", {})
                    .get("labels", {})
                    .get("lolday.job-id")
                )
        if not label:
            continue
        try:
            job_uuid = uuid.UUID(label)
        except ValueError:
            BACKEND_ERRORS.labels(stage="orphan_vcjob_malformed_label").inc()
            logger.warning("vcjob %s has malformed lolday.job-id %r", name, label)
            continue

        created_at_raw = meta.get("creationTimestamp")
        if created_at_raw:
            try:
                created_at = datetime.fromisoformat(
                    created_at_raw.replace("Z", "+00:00")
                )
            except ValueError:
                created_at = None
            if created_at and (now - created_at).total_seconds() < ORPHAN_GRACE_SECONDS:
                continue

        from app.models.job import Job  # avoid circular import at module load

        exists = await session.scalar(select(Job.id).where(Job.id == job_uuid))
        if exists is not None:
            continue

        vcjob_gone = False
        try:
            await asyncio.to_thread(
                volcano_v1alpha1().delete_namespaced_custom_object,
                group=VOLCANO_BATCH_GROUP,
                version=VOLCANO_BATCH_VERSION,
                namespace=settings.JOB_NAMESPACE,
                plural=VOLCANO_JOB_PLURAL,
                name=name,
                propagation_policy="Background",
            )
        except ApiException as exc:
            if exc.status == 404:
                vcjob_gone = True
            else:
                BACKEND_ERRORS.labels(stage="orphan_vcjob_delete").inc()
                logger.warning(
                    "orphan vcjob %s delete returned %s",
                    name,
                    exc.status,
                    exc_info=True,
                )
                continue

        # Reach the secret cleanup whether vcjob deleted just now or was
        # already gone — the orphan secret outlives a partial delete.
        try:
            await asyncio.to_thread(
                core_v1().delete_namespaced_secret,
                name=_job_token_secret_name(job_uuid),
                namespace=settings.JOB_NAMESPACE,
            )
        except ApiException as exc:
            if exc.status != 404:
                BACKEND_ERRORS.labels(stage="orphan_secret_delete").inc()
                logger.warning(
                    "orphan secret for vcjob %s delete returned %s",
                    name,
                    exc.status,
                    exc_info=True,
                )

        if not vcjob_gone:
            deleted += 1
        logger.info("deleted orphan vcjob %s (job-id %s)", name, job_uuid)

    return deleted
