import asyncio
import logging
import uuid
from functools import lru_cache

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_config() -> None:
    """Load in-cluster config (for running in Pod) or fallback to kubeconfig (local dev)."""
    try:
        config.load_incluster_config()
    except config.config_exception.ConfigException:
        config.load_kube_config()


def core_v1() -> client.CoreV1Api:
    load_config()
    return client.CoreV1Api()


def batch_v1() -> client.BatchV1Api:
    load_config()
    return client.BatchV1Api()


# Phase 7.3 — Volcano CRDs (`batch.volcano.sh/v1alpha1 Job`, `scheduling.volcano.sh/v1beta1
# Queue` / `PodGroup`) are accessed through the generic CustomObjectsApi. Training
# jobs go through this path; builds (kaniko) stay on batch_v1().
VOLCANO_BATCH_GROUP = "batch.volcano.sh"
VOLCANO_BATCH_VERSION = "v1alpha1"
VOLCANO_JOB_PLURAL = "jobs"

# Phase 2 — Volcano scheduling group (distinct from the batch group used for
# vcjob). Cluster-scoped resource, hence create_cluster_custom_object below.
VOLCANO_SCHED_GROUP = "scheduling.volcano.sh"
VOLCANO_SCHED_VERSION = "v1beta1"
VOLCANO_QUEUE_PLURAL = "queues"


def volcano_v1alpha1() -> client.CustomObjectsApi:
    load_config()
    return client.CustomObjectsApi()


# Per-user queue capability — matches the lolday-jobs-quota at the namespace
# level, so a single user can never exceed the cluster's workload allowance.
# DRF + proportion (already enabled by the Volcano sub-chart) handle
# fair-share between queues. Spec §6.3 OQ-1: gpu=2 (sum cap) lets a single
# user run one GPU2 job OR two GPU1 jobs concurrently; two GPU2 jobs
# from the same user are rejected by the queue, even if the cluster has
# capacity, so DRF still has a peer queue to schedule against.
_USER_QUEUE_CAPABILITY = {
    "cpu": "8",
    "memory": "30Gi",
    "nvidia.com/gpu": "2",
}


def queue_name_for_user(user_id: uuid.UUID) -> str:
    """Stable per-user Volcano queue name.

    12-hex prefix gives 16^12 = 2.8e14 unique names — enough for the
    foreseeable user count — and keeps the DNS-1123 length budget
    headroom (queue name + ``-podgroup-`` suffixes Volcano appends).
    """
    return f"lolday-u-{user_id.hex[:12]}"


async def ensure_user_queue(user_id: uuid.UUID) -> str:
    """Idempotently create a per-user Volcano Queue. Returns the queue name.

    Volcano Queue is cluster-scoped. K8s 409 (AlreadyExists) is treated as
    success — the queue may have been created by a previous request from
    the same user, or a parallel request racing this one. Any other
    ApiException propagates so the caller (routers/jobs.create_job) can
    return 5xx instead of silently submitting a job that has no queue.

    The underlying ``kubernetes`` client is sync; we run it via
    :func:`asyncio.to_thread` so a slow K8s API doesn't block the asyncio
    event loop alongside other request handlers.
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
        await asyncio.to_thread(
            volcano_v1alpha1().create_cluster_custom_object,
            group=VOLCANO_SCHED_GROUP,
            version=VOLCANO_SCHED_VERSION,
            plural=VOLCANO_QUEUE_PLURAL,
            body=body,
        )
        logger.info("created user queue %s", name)
    except ApiException as e:
        if e.status != 409:
            raise
        # 409 is the idempotent path; quiet.
    return name
