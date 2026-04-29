"""Harbor post-install initialization: projects + robot + retention + push Secret.

Runs idempotently on backend lifespan startup. Skips silently if
HARBOR_ADMIN_PASSWORD is unset (e.g. test environments).
"""

import base64
import json
import logging

from kubernetes.client import ApiException, V1ObjectMeta, V1Secret

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.services.harbor import HarborClient
from app.services.k8s import core_v1

logger = logging.getLogger(__name__)

PROJECTS = ("detectors", "detectors-cache", "lolday")
ROBOT_NAME = "build-pusher"
DETECTORS_RETENTION_KEEP_N = 3


async def init_harbor() -> None:
    """Idempotent: safe to run on every backend startup."""
    if not settings.HARBOR_ADMIN_PASSWORD:
        logger.warning("HARBOR_ADMIN_PASSWORD not set — skipping Harbor init")
        return

    client = HarborClient(
        settings.HARBOR_URL,
        settings.HARBOR_ADMIN_USERNAME,
        settings.HARBOR_ADMIN_PASSWORD,
    )

    for project in PROJECTS:
        try:
            await client.ensure_project(project, public=True)
        except Exception:
            BACKEND_ERRORS.labels(stage="ensure_project").inc()
            logger.exception("ensure_project failed for %s", project)

    try:
        robot = await client.ensure_robot_account(
            ROBOT_NAME,
            projects=list(PROJECTS),
        )
        # Fresh robot — persist docker config Secret; existing robot returns no secret
        if "secret" in robot:
            _write_docker_config_secret(robot["name"], robot["secret"])
    except Exception:
        BACKEND_ERRORS.labels(stage="ensure_robot").inc()
        logger.exception("ensure_robot_account failed")

    try:
        await client.set_retention_policy(
            "detectors", keep_n_recent=DETECTORS_RETENTION_KEEP_N
        )
    except Exception:
        BACKEND_ERRORS.labels(stage="retention_policy").inc()
        logger.exception("set_retention_policy failed for detectors")


def _write_docker_config_secret(robot_name: str, robot_secret: str) -> None:
    """Create/replace 'harbor-push-cred' Secret in BUILD_NAMESPACE so Kaniko can push."""
    registry = settings.HARBOR_IMAGE_PREFIX
    auth_blob = base64.b64encode(f"{robot_name}:{robot_secret}".encode()).decode()
    cfg = {"auths": {registry: {"auth": auth_blob}}}
    body = V1Secret(
        metadata=V1ObjectMeta(
            name="harbor-push-cred", namespace=settings.BUILD_NAMESPACE
        ),
        type="kubernetes.io/dockerconfigjson",
        string_data={".dockerconfigjson": json.dumps(cfg)},
    )
    try:
        core_v1().replace_namespaced_secret(
            name="harbor-push-cred",
            namespace=settings.BUILD_NAMESPACE,
            body=body,
        )
    except ApiException as e:
        if e.status == 404:
            core_v1().create_namespaced_secret(
                namespace=settings.BUILD_NAMESPACE, body=body
            )
        else:
            raise
