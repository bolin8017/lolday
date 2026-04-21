from functools import lru_cache

from kubernetes import client, config


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


def volcano_v1alpha1() -> client.CustomObjectsApi:
    load_config()
    return client.CustomObjectsApi()
