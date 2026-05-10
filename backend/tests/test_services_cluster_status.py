"""Tests for app.services.cluster_status — GPU allocation + Volcano queue queries."""

from contextlib import contextmanager
from datetime import UTC
from unittest.mock import patch

import pytest
from app.services import cluster_status
from app.services import gpu_signal as _gs


@pytest.fixture(autouse=True)
def _clear_cluster_caches():
    """Drop the TTL caches before each test so stubs don't bleed across cases."""
    cluster_status._gpu_cache.clear()
    cluster_status._queue_cache.clear()
    yield
    cluster_status._gpu_cache.clear()
    cluster_status._queue_cache.clear()


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


def _state(
    physical: int = 2,
    per_gpu: list | None = None,
    free: int = 2,
    lolday: int = 0,
    external: int = 0,
    fail_safe: bool = False,
    reason: str | None = None,
) -> _gs.GPUState:
    return _gs.GPUState(
        physical_total=physical,
        per_gpu=per_gpu or [],
        free_count=free,
        in_use_by_lolday_count=lolday,
        in_use_by_external_count=external,
        fail_safe_active=fail_safe,
        fail_safe_reason=reason,
    )


# --- GPU allocation ---


def test_get_gpu_allocation_returns_new_schema_all_free():
    statuses = [
        _gs.GPUStatus(0, False, False, 0.0, 0),
        _gs.GPUStatus(1, False, False, 0.0, 0),
    ]
    with patch(
        "app.services.cluster_status.gpu_signal.compute_real_gpu_state",
        return_value=_state(per_gpu=statuses, free=2),
    ):
        result = cluster_status.get_gpu_allocation()
    assert result["total"] == 2
    assert result["free_count"] == 2
    assert result["in_use_by_lolday"] == 0
    assert result["in_use_by_external"] == 0
    assert result["fail_safe_active"] is False
    assert result["per_gpu"] == [
        {"gpu_id": 0, "state": "free", "util_percent": 0.0, "vram_used_mb": 0},
        {"gpu_id": 1, "state": "free", "util_percent": 0.0, "vram_used_mb": 0},
    ]


def test_get_gpu_allocation_marks_lolday_and_external_states():
    statuses = [
        _gs.GPUStatus(0, True, False, 87.5, 9240),
        _gs.GPUStatus(1, False, True, 54.0, 7200),
    ]
    with patch(
        "app.services.cluster_status.gpu_signal.compute_real_gpu_state",
        return_value=_state(per_gpu=statuses, free=0, lolday=1, external=1),
    ):
        result = cluster_status.get_gpu_allocation()
    assert result["per_gpu"][0]["state"] == "lolday"
    assert result["per_gpu"][1]["state"] == "external"
    assert result["in_use_by_lolday"] == 1
    assert result["in_use_by_external"] == 1
    assert result["free_count"] == 0


def test_get_gpu_allocation_fail_safe_propagates():
    with patch(
        "app.services.cluster_status.gpu_signal.compute_real_gpu_state",
        return_value=_state(free=0, fail_safe=True, reason="Prom timeout"),
    ):
        result = cluster_status.get_gpu_allocation()
    assert result["fail_safe_active"] is True
    assert result["fail_safe_reason"] == "Prom timeout"
    assert result["free_count"] == 0
    assert result["per_gpu"] == []


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
    lolday_volcano_pending_stale gauge so an alert can fire on scheduler
    hangs."""
    from datetime import datetime, timedelta

    from app.metrics import VOLCANO_PENDING_STALE

    now = datetime.now(UTC)
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


def test_stale_gauge_drops_to_zero_when_no_stale_pending():
    """Gauge must reset to 0 when the stale condition clears — `.set(0)`
    must be reached on the happy path, not short-circuited."""
    from datetime import datetime, timedelta

    from app.metrics import VOLCANO_PENDING_STALE

    # Seed a non-zero value first (simulate "earlier tick said 3 stale").
    VOLCANO_PENDING_STALE.set(3)
    now = datetime.now(UTC)
    fresh = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = [
        _vjob("a", "lolday-training", "Pending", fresh),
        _vjob("b", "lolday-training", "Running", fresh),
    ]
    with _patched_volcano(items):
        cluster_status.get_queue_depth()
    assert VOLCANO_PENDING_STALE._value.get() == 0.0


def test_stale_gauge_survives_bad_creationtimestamp():
    """A single malformed timestamp in the Volcano CR list must NOT crash
    get_queue_depth — else the gauge would stick at its previous value and
    the alert could falsely fire or falsely silence."""
    from datetime import datetime, timedelta

    from app.metrics import VOLCANO_PENDING_STALE

    now = datetime.now(UTC)
    stale = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = [
        _vjob("good", "lolday-training", "Pending", stale),
        {
            "metadata": {"name": "bad", "creationTimestamp": "not-a-date"},
            "spec": {"queue": "lolday-training"},
            "status": {"state": {"phase": "Pending"}},
        },
    ]
    with _patched_volcano(items):
        depth = cluster_status.get_queue_depth()
    # good job counted; bad job skipped without raising
    assert depth == 2
    assert VOLCANO_PENDING_STALE._value.get() == 1.0


def test_parse_iso8601_returns_none_on_bad_input():
    assert cluster_status._parse_iso8601(None) is None
    assert cluster_status._parse_iso8601("") is None
    assert cluster_status._parse_iso8601("not-a-date") is None
    # Happy path still works
    parsed = cluster_status._parse_iso8601("2026-04-21T01:00:00Z")
    assert parsed is not None
    assert parsed.year == 2026


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
