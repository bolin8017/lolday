"""Defensive-path tests for `_cleanup_job_secret` in
`app/reconciler/jobs.py`.

Three terminal transition sites (timeout, succeeded, failed) call this
helper. The error handling — 404 swallowed silently, non-404 increments
`BACKEND_ERRORS{stage="k8s_cleanup"}` + logs — was uncovered.
"""

from __future__ import annotations

import uuid as _uuid
from unittest.mock import MagicMock, patch

import pytest
from app.models.job import Job
from app.reconciler.jobs import _cleanup_job_secret
from kubernetes.client import ApiException
from prometheus_client import REGISTRY


def _stub_job() -> Job:
    """Minimal Job stub; only `id` is read by _cleanup_job_secret."""
    j = MagicMock(spec=Job)
    j.id = _uuid.uuid4()
    return j


def _get(stage: str) -> float:
    return (
        REGISTRY.get_sample_value("lolday_backend_errors_total", {"stage": stage})
        or 0.0
    )


@pytest.mark.asyncio
async def test_cleanup_succeeds_silently():
    """Happy path: delete returns normally — no exception, no counter bump."""
    core_stub = MagicMock()
    core_stub.delete_namespaced_secret = MagicMock(return_value=None)

    before = _get("k8s_cleanup")
    with patch("app.reconciler.jobs.core_v1", return_value=core_stub):
        await _cleanup_job_secret(_stub_job())
    core_stub.delete_namespaced_secret.assert_called_once()
    assert _get("k8s_cleanup") == before


@pytest.mark.asyncio
async def test_cleanup_404_is_silent():
    """ApiException(404) means the Secret was already gone (e.g. K8s GC
    won the race) — swallow silently without bumping the counter."""
    core_stub = MagicMock()
    core_stub.delete_namespaced_secret = MagicMock(side_effect=ApiException(status=404))

    before = _get("k8s_cleanup")
    with patch("app.reconciler.jobs.core_v1", return_value=core_stub):
        await _cleanup_job_secret(_stub_job())
    assert _get("k8s_cleanup") == before


@pytest.mark.asyncio
async def test_cleanup_non_404_increments_metric():
    """Non-404 ApiException (e.g. 500) increments
    `BACKEND_ERRORS{stage="k8s_cleanup"}` and logs. The exception is
    swallowed because the calling terminal transition has already
    committed — re-raising would leak the cleanup-side failure into the
    reconciler iteration counter."""
    core_stub = MagicMock()
    core_stub.delete_namespaced_secret = MagicMock(
        side_effect=ApiException(status=500, reason="server error")
    )

    before = _get("k8s_cleanup")
    with patch("app.reconciler.jobs.core_v1", return_value=core_stub):
        # Must NOT raise.
        await _cleanup_job_secret(_stub_job())
    assert _get("k8s_cleanup") == before + 1.0
