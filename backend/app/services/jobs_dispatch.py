"""Volcano vcjob dispatch: create vcjob + token Secret, transition job status.

Extracted from ``app.routers.jobs`` POST /jobs so both the HTTP handler (Phase
6d onward: will be used only until Task E lands) and the FIFO reconciler can
share the same submission path.

Idempotency notes:
- The K8s vcjob create is attempted first.  If it fails the DB row is NOT
  committed (we raise; the caller's session stays dirty for a rollback /
  retry).
- A fresh raw token is generated on each invocation and ``job.token_hash``
  is updated atomically with the status transition.  If the K8s call fails
  and the caller retries, the token is refreshed — the container uses
  whatever was last committed, so there is no stale-secret risk.
"""

from __future__ import annotations

import contextlib
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DetectorVersion
from app.models.job import Job, JobStatus
from app.services.job_config import resolve_source_model_path
from app.services.job_spec import build_job_token_secret, build_volcano_job_manifest
from app.services.job_tokens import generate_token, hash_token
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    core_v1,
    ensure_user_queue,
    volcano_v1alpha1,
)

logger = logging.getLogger(__name__)

_KNOWN_DISTRIBUTED_STRATEGIES = frozenset({"ddp", "fsdp", "deepspeed"})


def _strategy_from_manifest_dict(manifest: dict | None) -> str:
    """Resolve the Lightning distributed strategy string from a raw manifest dict.

    Mirrors ``app.routers.jobs._strategy_from_manifest`` but accepts a plain
    ``dict`` (as stored in ``DetectorVersion.manifest``) rather than a
    ``DetectorManifest`` Pydantic instance, so the reconciler does not need
    to re-parse the manifest into a model object.

    Falls back to ``"ddp"`` for ``True`` / missing values (legacy manifests).
    Raises ``ValueError`` for an unknown strategy string so callers can surface
    the misconfiguration.
    """
    if manifest is None:
        return "ddp"
    val = (manifest.get("lifecycle") or {}).get("supports_distributed", False)
    if isinstance(val, str):
        if val not in _KNOWN_DISTRIBUTED_STRATEGIES:
            raise ValueError(
                f"manifest.lifecycle.supports_distributed={val!r} is not a "
                f"known strategy; expected one of "
                f"{sorted(_KNOWN_DISTRIBUTED_STRATEGIES)}"
            )
        return val
    return "ddp"


async def dispatch_job_to_volcano(session: AsyncSession, job: Job) -> None:
    """Create vcjob + token Secret, transition ``job.status`` to PREPARING.

    The caller is responsible for committing (or rolling back) the session
    after this function returns.  This keeps the function testable and avoids
    double-commit when the router already manages its own session commit.

    Raises on any K8s API failure; the job remains at its current status so
    the caller (reconciler or router) can decide how to handle the error.
    """
    # Load the DetectorVersion to get harbor_image, mlflow IDs, and manifest.
    dv = await session.get(DetectorVersion, job.detector_version_id)
    if dv is None:
        raise RuntimeError(
            f"FK invariant violated: job {job.id} references missing "
            f"DetectorVersion {job.detector_version_id}"
        )

    # Resolve GPU strategy from the raw manifest dict.
    gpu_strategy = _strategy_from_manifest_dict(dv.manifest)

    # Resolve source model artifact path (evaluate / predict jobs).
    source_run_id = None
    if job.source_model_version_id is not None:
        from app.models import ModelVersion

        mv = await session.get(ModelVersion, job.source_model_version_id)
        if mv is not None:
            source_run_id = mv.mlflow_run_id

    source_artifact_path = (
        resolve_source_model_path(f"runs:/{source_run_id}/model")
        if source_run_id
        else None
    )

    # Generate a fresh job token and update the stored hash.
    raw_token = generate_token()
    job.token_hash = hash_token(raw_token)

    # Create the token Secret before the vcjob so the init container can read
    # it immediately when the pod starts.
    secret = build_job_token_secret(job.id, raw_token)
    core_v1().create_namespaced_secret(namespace=settings.JOB_NAMESPACE, body=secret)

    # Ensure the per-user Volcano queue exists (idempotent).
    queue_name = ensure_user_queue(job.owner_id)

    manifest = build_volcano_job_manifest(
        job_id=job.id,
        job_type=job.type,
        detector_image=dv.harbor_image,
        mlflow_experiment_id=job.mlflow_experiment_id or "",
        mlflow_run_id=job.mlflow_run_id or "",
        mlflow_tracking_uri=settings.MLFLOW_TRACKING_URI,
        source_run_id=source_run_id,
        source_artifact_path=source_artifact_path,
        internal_events_url=(
            f"{settings.INTERNAL_EVENTS_BASE_URL}/api/v1/internal/jobs/{job.id}/events"
        ),
        queue_name=queue_name,
        resource_profile=job.resource_profile,
        gpu_strategy=gpu_strategy,
        active_deadline_seconds=job.active_deadline_seconds,
    )

    try:
        volcano_v1alpha1().create_namespaced_custom_object(
            group=VOLCANO_BATCH_GROUP,
            version=VOLCANO_BATCH_VERSION,
            namespace=settings.JOB_NAMESPACE,
            plural=VOLCANO_JOB_PLURAL,
            body=manifest,
        )
    except Exception:
        # Roll back the token secret we just created so we leave no orphaned
        # secrets behind on a partial failure.
        with contextlib.suppress(Exception):
            core_v1().delete_namespaced_secret(
                name=secret["metadata"]["name"],
                namespace=settings.JOB_NAMESPACE,
            )
        raise

    job.k8s_job_name = manifest["metadata"]["name"]
    job.status = JobStatus.PREPARING
