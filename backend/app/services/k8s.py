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
