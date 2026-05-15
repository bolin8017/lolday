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


def _extract_vcjob_label(vj: dict) -> str | None:
    """Extract the ``lolday.job-id`` label from a Volcano Job, falling back to
    the task pod template's labels for older vcjobs / chart variants that
    only set the label on the pod template.
    """
    meta = vj.get("metadata") or {}
    label = (meta.get("labels") or {}).get("lolday.job-id")
    if label:
        return label
    tasks = (vj.get("spec") or {}).get("tasks") or []
    if tasks:
        return (
            (tasks[0].get("template") or {})
            .get("metadata", {})
            .get("labels", {})
            .get("lolday.job-id")
        )
    return None


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
        # task pod template — _extract_vcjob_label reads the top-level copy
        # first (survives task restructuring), with the deeper path as a
        # fallback for older vcjobs / chart variants that only set the label
        # on the pod template.
        label = _extract_vcjob_label(vjob)
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


TOKEN_SECRET_PREFIX = "job-token-"


async def reconcile_orphan_token_secrets(session: AsyncSession) -> int:
    """Delete ``job-token-*`` Secrets whose parent vcjob is gone.

    The ``ownerReferences``-driven GC handles the happy path (vcjob deleted
    normally → Secret deleted by the K8s GC controller). This sweep catches
    the exception path: ``kubectl delete vcjob ... --grace-period=0 --force``
    removes the vcjob without firing finalizers or the GC controller,
    leaving the Secret as an orphan.

    We list every Secret in JOB_NAMESPACE (plus any namespace listed in
    ``JOB_TOKEN_LEGACY_NAMESPACES``) matching the ``job-token-`` name
    prefix, check each one's age + whether a matching vcjob exists, and
    delete those that are both stale (older than
    ``JOB_TTL_SECONDS_AFTER_FINISHED``) and unowned (no matching vcjob).

    #175: a 2026-05-05 ns migration moved live vcjob traffic from
    ``lolday`` to ``lolday-jobs`` but the sweep stayed scoped to the new
    namespace only, leaving 718 stale Secrets in ``lolday``. The sweep
    now iterates ``[JOB_NAMESPACE, *JOB_TOKEN_LEGACY_NAMESPACES]`` so a
    future ns split doesn't repeat the bug.

    Returns the total number of orphan Secrets deleted across all
    in-scope namespaces, for metrics.
    """
    # Vcjob liveness is computed from the *current* JOB_NAMESPACE only --
    # legacy namespaces are pure cleanup targets and should not be looked
    # up for live vcjobs (a stale vcjob in a legacy ns would extend the
    # life of an unrelated Secret in the live ns by short-id collision,
    # which is rare but worse than the alternative of cleaning eagerly).
    vcjobs = await asyncio.to_thread(
        volcano_v1alpha1().list_namespaced_custom_object,
        group=VOLCANO_BATCH_GROUP,
        version=VOLCANO_BATCH_VERSION,
        namespace=settings.JOB_NAMESPACE,
        plural=VOLCANO_JOB_PLURAL,
    )

    # Build a set of live job-short-ids from the vcjob labels. The Secret
    # name pattern is ``job-token-<job.hex[:16]>``; the vcjob label
    # ``lolday.job-id`` carries the full UUID. Match on the 16-char prefix.
    # _extract_vcjob_label falls back to the task pod template labels for
    # older vcjobs / chart variants that only set the label there.
    live_short_ids: set[str] = set()
    for vj in vcjobs.get("items", []):
        label = _extract_vcjob_label(vj)
        if label:
            try:
                live_short_ids.add(uuid.UUID(label).hex[:16])
            except ValueError:
                continue

    now = datetime.now(UTC)
    ttl = settings.JOB_TTL_SECONDS_AFTER_FINISHED
    # #175: dedup the namespace list so a misconfiguration that puts
    # JOB_NAMESPACE into the legacy list doesn't cause us to scan twice.
    sweep_namespaces: list[str] = []
    for ns in [settings.JOB_NAMESPACE, *settings.JOB_TOKEN_LEGACY_NAMESPACES]:
        if ns not in sweep_namespaces:
            sweep_namespaces.append(ns)

    deleted = 0
    for ns in sweep_namespaces:
        deleted += await _sweep_orphan_token_secrets_in_namespace(
            namespace=ns, live_short_ids=live_short_ids, ttl=ttl, now=now
        )
    return deleted


async def _sweep_orphan_token_secrets_in_namespace(
    *,
    namespace: str,
    live_short_ids: set[str],
    ttl: int,
    now: datetime,
) -> int:
    """List + delete orphan ``job-token-*`` Secrets in a single namespace.

    Extracted as a helper so the per-namespace logic stays single-source
    while the outer caller loops over the current + legacy namespaces.
    """
    secrets = await asyncio.to_thread(
        core_v1().list_namespaced_secret,
        namespace=namespace,
    )
    deleted = 0
    for sec in secrets.items:
        # Conftest stub passes dicts; real K8s passes objects. Handle both.
        if isinstance(sec, dict):
            meta = sec.get("metadata", {})
        else:
            meta = {
                "name": sec.metadata.name,
                "creationTimestamp": sec.metadata.creation_timestamp,
            }
        name = meta.get("name", "")
        if not name.startswith(TOKEN_SECRET_PREFIX):
            continue
        # Age check.
        created_raw = meta.get("creationTimestamp")
        if isinstance(created_raw, datetime):
            created_at = created_raw
        elif isinstance(created_raw, str):
            try:
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
        else:
            continue
        age = (now - created_at).total_seconds()
        if age < ttl:
            continue
        # Liveness check by short-id prefix.
        short_id = name.removeprefix(TOKEN_SECRET_PREFIX)
        if short_id in live_short_ids:
            continue
        # Delete.
        try:
            await asyncio.to_thread(
                core_v1().delete_namespaced_secret,
                name=name,
                namespace=namespace,
            )
            deleted += 1
            logger.info(
                "deleted orphan job-token Secret %s/%s (age=%.0fs)",
                namespace,
                name,
                age,
            )
        except ApiException as exc:
            if exc.status != 404:
                BACKEND_ERRORS.labels(stage="orphan_token_secret_delete").inc()
                logger.warning(
                    "orphan token Secret %s/%s delete returned %s",
                    namespace,
                    name,
                    exc.status,
                    exc_info=True,
                )
    return deleted
