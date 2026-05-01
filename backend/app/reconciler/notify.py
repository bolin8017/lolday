"""Discord-notification helpers for the reconciler.

These helpers are shared by both the build path (``builds.py`` /
``build_finalize.py``) and the job path (``jobs.py``):

- :class:`NotifyContext` and :func:`_user_context` resolve a Discord identity
  from a User row, returning ``None`` for service-token principals so callers
  can early-return (machine activity does not need user-event notifications).
- :func:`_detector_label`, :func:`_ui_url`, :func:`_primary_metric` are small
  formatters used inside ``notify_*`` payloads.
- :func:`_fire_job_failed_notify` is the shared ``notify_job_failed`` dispatch
  used by all 3 terminal-failure paths in :func:`reconcile_job`
  (Volcano Failed/Aborted, wall-clock TIMEOUT, k8s_job_missing 404).
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.notify import notify_job_failed


@dataclass(frozen=True)
class NotifyContext:
    """Discord-embed identity for a single notification.

    Returned from :func:`_user_context`; ``None`` from that helper means
    "skip notify" (the user is a CF Access service-token principal whose
    events would only dilute the user-event channel).
    """

    name: str
    discord_id: str | None


async def _user_context(session: AsyncSession, user_id) -> NotifyContext | None:
    """Resolve a notification identity, or ``None`` to signal "skip notify".

    ``name`` falls back through display_name → email local-part → literal
    "user" (the last case only triggers when the user row is missing
    entirely, since email is required on User).

    Service-token principals yield ``None`` so every notify_* callsite
    can early-return. Service-token activity is automated and not
    actionable by humans — its events would only dilute the user-event
    Discord channel.
    """
    from app.models import User

    user = await session.get(User, user_id)
    if user is None:
        return NotifyContext(name="unknown", discord_id=None)
    if user.is_service_token:
        return None
    name = user.display_name or (user.email.split("@")[0] if user.email else "user")
    return NotifyContext(name=name, discord_id=user.discord_user_id)


async def _detector_label(session: AsyncSession, detector_id) -> str:
    """Returns detector.display_name, or "unknown" if the row was deleted."""
    from app.models import Detector

    det = await session.get(Detector, detector_id)
    if det is None:
        return "unknown"
    return det.display_name


def _ui_url(path: str) -> str:
    """Absolute UI link built from `settings.LOLDAY_UI_BASE_URL`."""
    return f"{settings.LOLDAY_UI_BASE_URL.rstrip('/')}{path}"


def _primary_metric(metrics: dict) -> tuple[str, float] | None:
    """Returns the first available metric in priority order f1 > accuracy >
    precision > recall; None if none are numeric."""
    for key in ("f1", "accuracy", "precision", "recall"):
        val = metrics.get(key)
        if isinstance(val, int | float):
            return (key, float(val))
    return None


async def _fire_job_failed_notify(
    session: AsyncSession,
    j,
    reason: str,
) -> None:
    """Schedule a job-failed Discord notify without blocking the reconciler.

    Shared helper for the 3 terminal-failure paths: Volcano Failed/Aborted
    phase, wall-clock TIMEOUT, and k8s_job_missing (404 on GET).
    """
    from app.models import DatasetConfig, DetectorVersion

    ctx = await _user_context(session, j.owner_id)
    if ctx is None:
        return
    dv = await session.get(DetectorVersion, j.detector_version_id)
    det_label = await _detector_label(session, dv.detector_id) if dv else "unknown"
    detector_label = f"{det_label} {dv.git_tag}" if dv else det_label
    dataset_name = None
    ds_id = j.train_dataset_id or j.test_dataset_id or j.predict_dataset_id
    if ds_id:
        ds = await session.get(DatasetConfig, ds_id)
        dataset_name = ds.name if ds else None
    asyncio.create_task(  # noqa: RUF006  # fire-and-forget notification task
        notify_job_failed(
            user_name=ctx.name,
            user_discord_id=ctx.discord_id,
            job_type=j.type.value,
            detector_label=detector_label,
            dataset_name=dataset_name,
            failure_reason=reason,
            job_url=_ui_url(f"/jobs/{j.id}"),
        )
    )
