"""Tests for app.services.cluster_status — GPU allocation + Volcano queue queries."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import cluster_status


@pytest.fixture(autouse=True)
def _clear_cluster_caches():
    """Drop the TTL caches before each test so stubs don't bleed across cases."""
    cluster_status._gpu_cache.clear()
    cluster_status._queue_cache.clear()
    yield
    cluster_status._gpu_cache.clear()
    cluster_status._queue_cache.clear()


def _node(gpu_count: str):
    return SimpleNamespace(
        status=SimpleNamespace(allocatable={"nvidia.com/gpu": gpu_count})
    )


def _pod_with_gpu(phase: str, gpu: str | None):
    containers = [
        SimpleNamespace(
            resources=SimpleNamespace(limits={"nvidia.com/gpu": gpu} if gpu else {})
        )
    ]
    return SimpleNamespace(
        status=SimpleNamespace(phase=phase),
        spec=SimpleNamespace(containers=containers),
    )


@contextmanager
def _patched_core(nodes, pods):
    class Stub:
        def list_node(self):
            return SimpleNamespace(items=nodes)
        def list_namespaced_pod(self, namespace=None):
            # Phase 7.5: get_gpu_allocation now reads only from the job
            # namespace. The stub returns the seeded list regardless of the
            # namespace arg since tests don't exercise cross-ns filtering.
            return SimpleNamespace(items=pods)
    with patch("app.services.cluster_status.core_v1", return_value=Stub()):
        yield


@contextmanager
def _patched_volcano(items):
    class Stub:
        def list_namespaced_custom_object(self, **kwargs):
            return {"items": items}
    with patch("app.services.cluster_status.volcano_v1alpha1", return_value=Stub()):
        yield


def _vjob(name: str, queue: str, phase: str | None, created: str):
    status = {"state": {"phase": phase}} if phase is not None else {}
    return {
        "metadata": {"name": name, "creationTimestamp": created},
        "spec": {"queue": queue},
        "status": status,
    }


# --- GPU allocation ---

def test_get_gpu_allocation_sums_node_allocatable():
    with _patched_core([_node("2"), _node("1")], []):
        result = cluster_status.get_gpu_allocation()
    assert result["total"] == 3


def test_get_gpu_allocation_counts_running_pods_with_gpu_limit():
    with _patched_core(
        [_node("2")],
        [_pod_with_gpu("Running", "1"), _pod_with_gpu("Running", "1")],
    ):
        result = cluster_status.get_gpu_allocation()
    assert result == {"total": 2, "in_use": 2, "idle": 0}


def test_get_gpu_allocation_ignores_non_running_pods():
    with _patched_core(
        [_node("2")],
        [_pod_with_gpu("Pending", "1"), _pod_with_gpu("Succeeded", "1")],
    ):
        result = cluster_status.get_gpu_allocation()
    assert result == {"total": 2, "in_use": 0, "idle": 2}


def test_get_gpu_allocation_ignores_pods_without_gpu_limit():
    with _patched_core(
        [_node("2")],
        [_pod_with_gpu("Running", None), _pod_with_gpu("Running", "1")],
    ):
        result = cluster_status.get_gpu_allocation()
    assert result == {"total": 2, "in_use": 1, "idle": 1}


def test_get_gpu_allocation_zero_nodes():
    with _patched_core([], []):
        result = cluster_status.get_gpu_allocation()
    assert result == {"total": 0, "in_use": 0, "idle": 0}


def test_get_gpu_allocation_idle_clamped_non_negative():
    # Over-subscribed edge case: if accounting drifts, never return negative idle.
    with _patched_core(
        [_node("1")],
        [_pod_with_gpu("Running", "2")],
    ):
        result = cluster_status.get_gpu_allocation()
    assert result["idle"] == 0


# --- Queue depth ---

def test_get_queue_depth_skips_terminal_phases():
    items = [
        _vjob("a", "lolday-training", "Running", "2026-04-21T01:00:00Z"),
        _vjob("b", "lolday-training", "Pending", "2026-04-21T01:01:00Z"),
        _vjob("c", "lolday-training", "Completed", "2026-04-21T00:00:00Z"),
        _vjob("d", "lolday-training", "Failed", "2026-04-21T00:00:00Z"),
        _vjob("e", "lolday-training", "Aborted", "2026-04-21T00:00:00Z"),
        _vjob("f", "lolday-training", "Terminated", "2026-04-21T00:00:00Z"),
    ]
    with _patched_volcano(items):
        assert cluster_status.get_queue_depth() == 2


def test_get_queue_depth_filters_by_queue_name():
    items = [
        _vjob("a", "lolday-training", "Running", "2026-04-21T01:00:00Z"),
        _vjob("b", "other-queue", "Running", "2026-04-21T01:01:00Z"),
    ]
    with _patched_volcano(items):
        assert cluster_status.get_queue_depth() == 1


def test_get_queue_depth_handles_missing_status_state():
    # Freshly created Volcano Jobs have no status.state yet — count as queued.
    items = [_vjob("a", "lolday-training", None, "2026-04-21T01:00:00Z")]
    with _patched_volcano(items):
        assert cluster_status.get_queue_depth() == 1


def test_get_queue_depth_updates_stale_gauge():
    """Pending jobs older than VOLCANO_STALE_SECONDS are reflected in the
    lolday_volcano_pending_stale_total gauge so an alert can fire on
    scheduler hangs."""
    from datetime import datetime, timedelta, timezone
    from app.metrics import VOLCANO_PENDING_STALE

    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = [
        _vjob("fresh", "lolday-training", "Pending", fresh),
        _vjob("stale1", "lolday-training", "Pending", stale),
        _vjob("stale2", "lolday-training", "Inqueue", stale),
        _vjob("done", "lolday-training", "Completed", stale),  # excluded: terminal
    ]
    with _patched_volcano(items):
        cluster_status.get_queue_depth()
    assert VOLCANO_PENDING_STALE._value.get() == 2.0


# --- Queue position ---

def test_get_job_queue_position_1indexed_by_creation_order():
    items = [
        _vjob("b", "lolday-training", "Pending", "2026-04-21T01:02:00Z"),
        _vjob("a", "lolday-training", "Pending", "2026-04-21T01:01:00Z"),
        _vjob("c", "lolday-training", "Pending", "2026-04-21T01:03:00Z"),
    ]
    with _patched_volcano(items):
        assert cluster_status.get_job_queue_position("a") == 1
        assert cluster_status.get_job_queue_position("b") == 2
        assert cluster_status.get_job_queue_position("c") == 3


def test_get_job_queue_position_none_when_job_running():
    items = [
        _vjob("a", "lolday-training", "Running", "2026-04-21T01:00:00Z"),
        _vjob("b", "lolday-training", "Pending", "2026-04-21T01:01:00Z"),
    ]
    with _patched_volcano(items):
        assert cluster_status.get_job_queue_position("a") is None


def test_get_job_queue_position_none_when_job_missing():
    items = [_vjob("a", "lolday-training", "Pending", "2026-04-21T01:00:00Z")]
    with _patched_volcano(items):
        assert cluster_status.get_job_queue_position("does-not-exist") is None


def test_get_job_queue_position_ignores_other_queues():
    items = [
        _vjob("a", "other", "Pending", "2026-04-21T01:00:00Z"),
        _vjob("b", "lolday-training", "Pending", "2026-04-21T01:01:00Z"),
    ]
    with _patched_volcano(items):
        assert cluster_status.get_job_queue_position("a") is None
        assert cluster_status.get_job_queue_position("b") == 1
