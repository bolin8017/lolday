"""Harbor robot account rotation.

Renews the ``build-pusher`` robot's secret and resets its expiry whenever
it's within ``HARBOR_ROBOT_RENEW_THRESHOLD_SECONDS`` of expiry. Also
force-rotates the one-time legacy ``duration=-1`` (no-expiry) robot left
over from before T13 — on the first reconciler tick after this code
ships, that robot gets a fresh secret + a finite expiry of
``HARBOR_ROBOT_RENEW_DURATION_DAYS`` days.

Invoked from :func:`app.reconciler.loop.reconciler_loop` every
``HARBOR_ROTATE_EVERY_N_ITERATIONS`` iterations (~24 h at the default 10 s
reconciler tick). The check is cheap (one GET /robots) so daily cadence
is fine — the actual rotation happens at most quarterly per robot.

Unit notes: Harbor's ``duration`` field is in DAYS per the v2.x swagger
(``api/v2.0/swagger.yaml`` line 7800), hence the ``_DAYS`` suffix on the
renewal constant. ``expires_at`` is an epoch-second timestamp, so the
threshold constant stays in seconds (compared against ``expires_at -
now_epoch``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.services.harbor import HarborClient

# Reuse _write_docker_config_secret from harbor_init: it's marked private by
# convention (leading underscore), but the rotation flow needs to refresh the
# same in-cluster ``harbor-push-cred`` Secret that init writes. Per the T14
# plan, the alternative would be to duplicate the writer; reuse keeps the
# single-source-of-truth invariant on the Secret body shape.
from app.services.harbor_init import ROBOT_NAME, _write_docker_config_secret

logger = logging.getLogger(__name__)

HARBOR_ROBOT_RENEW_DURATION_DAYS = (
    90  # Harbor `duration` is in days (matches harbor.py default)
)
HARBOR_ROBOT_RENEW_THRESHOLD_SECONDS = 30 * 86400  # rotate when <30 d remaining


async def reconcile_harbor_robot() -> bool:
    """Decide whether to rotate the build-pusher robot; rotate if needed.

    Returns True if a rotation was performed, False otherwise (no robot
    exists yet, or expiry is comfortably in the future). Exceptions
    propagate to the reconciler loop's per-iteration try/except (which
    counts the error and logs).
    """
    if not settings.HARBOR_ADMIN_PASSWORD:
        # Identical guard to init_harbor — test envs run without Harbor.
        return False

    client = HarborClient(
        settings.HARBOR_URL,
        settings.HARBOR_ADMIN_USERNAME,
        settings.HARBOR_ADMIN_PASSWORD,
    )
    robot = await client.get_robot(ROBOT_NAME)
    if robot is None:
        # Robot doesn't exist yet — init_harbor (lifespan) is the right place
        # to create it. We're a renewal loop, not a bootstrap.
        return False

    duration = robot.get("duration", 0)
    expires_at = robot.get("expires_at", 0)
    robot_id = robot["id"]
    now_epoch = int(datetime.now(UTC).timestamp())

    # Legacy duration=-1 robot: force-rotate on first pass after T13.
    legacy_neg1 = duration == -1
    # Normal renewal: <30 d remaining.
    expiring_soon = (
        expires_at > 0
        and (expires_at - now_epoch) < HARBOR_ROBOT_RENEW_THRESHOLD_SECONDS
    )

    if not (legacy_neg1 or expiring_soon):
        return False

    reason = (
        "legacy duration=-1"
        if legacy_neg1
        else f"expires in {(expires_at - now_epoch) // 86400} d"
    )
    logger.info("rotating Harbor robot %s (reason: %s)", robot["name"], reason)

    try:
        # Extend / reset the expiry first — if rotate_robot_secret succeeds and
        # update fails, we'd leave the next reconciler tick to retry from a
        # known-good state (the new secret is already in the Harbor record).
        await client.update_robot_duration(robot_id, HARBOR_ROBOT_RENEW_DURATION_DAYS)
        new_secret = await client.rotate_robot_secret(robot_id)
    except Exception:
        BACKEND_ERRORS.labels(stage="harbor_robot_rotate_api").inc()
        raise

    try:
        await _write_docker_config_secret(robot["name"], new_secret)
    except Exception:
        BACKEND_ERRORS.labels(stage="harbor_robot_rotate_k8s").inc()
        logger.exception(
            "harbor robot rotated but harbor-push-cred Secret write failed — "
            "next build will use the new secret only after the Secret is "
            "manually re-applied"
        )
        raise

    logger.info("Harbor robot %s rotated (new expiry +90 d)", robot["name"])
    return True
