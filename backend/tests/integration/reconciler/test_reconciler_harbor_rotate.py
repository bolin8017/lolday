"""Tests for app.reconciler.harbor_rotate — quarterly renewal + one-time
force-rotate of legacy duration=-1 robots."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_reconcile_force_rotates_legacy_duration_neg1_robot(monkeypatch):
    """L-harbor-robot-rotate: a robot with duration=-1 (legacy, never expires)
    is force-rotated unconditionally on the first reconciler pass."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")
    from app.reconciler import harbor_rotate

    mock_client = MagicMock()
    mock_client.get_robot = AsyncMock(
        return_value={
            "id": 42,
            "name": "robot$build-pusher",
            "duration": -1,
            "expires_at": -1,
        }
    )
    mock_client.update_robot_duration = AsyncMock()
    mock_client.rotate_robot_secret = AsyncMock(return_value="fresh-secret")

    written = []

    async def fake_writer(name, secret):
        written.append((name, secret))

    with (
        patch("app.reconciler.harbor_rotate.HarborClient", return_value=mock_client),
        patch(
            "app.reconciler.harbor_rotate._write_docker_config_secret",
            fake_writer,
        ),
    ):
        rotated = await harbor_rotate.reconcile_harbor_robot()

    assert rotated is True
    mock_client.update_robot_duration.assert_awaited_once_with(42, 90)
    mock_client.rotate_robot_secret.assert_awaited_once_with(42)
    assert written == [("robot$build-pusher", "fresh-secret")]


@pytest.mark.asyncio
async def test_reconcile_rotates_robot_within_30_day_threshold(monkeypatch):
    """A robot that expires in <30 d is rotated."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")
    from app.reconciler import harbor_rotate

    soon = int((datetime.now(UTC) + timedelta(days=15)).timestamp())
    mock_client = MagicMock()
    mock_client.get_robot = AsyncMock(
        return_value={
            "id": 7,
            "name": "robot$build-pusher",
            "duration": 90,
            "expires_at": soon,
        }
    )
    mock_client.update_robot_duration = AsyncMock()
    mock_client.rotate_robot_secret = AsyncMock(return_value="s")

    with (
        patch("app.reconciler.harbor_rotate.HarborClient", return_value=mock_client),
        patch(
            "app.reconciler.harbor_rotate._write_docker_config_secret",
            AsyncMock(),
        ),
    ):
        rotated = await harbor_rotate.reconcile_harbor_robot()

    assert rotated is True


@pytest.mark.asyncio
async def test_reconcile_skips_robot_outside_threshold(monkeypatch):
    """A robot expiring in >30 d is left alone."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")
    from app.reconciler import harbor_rotate

    far = int((datetime.now(UTC) + timedelta(days=60)).timestamp())
    mock_client = MagicMock()
    mock_client.get_robot = AsyncMock(
        return_value={
            "id": 7,
            "name": "robot$build-pusher",
            "duration": 90,
            "expires_at": far,
        }
    )
    mock_client.update_robot_duration = AsyncMock()
    mock_client.rotate_robot_secret = AsyncMock(return_value="s")

    with (
        patch("app.reconciler.harbor_rotate.HarborClient", return_value=mock_client),
        patch(
            "app.reconciler.harbor_rotate._write_docker_config_secret",
            AsyncMock(),
        ),
    ):
        rotated = await harbor_rotate.reconcile_harbor_robot()

    assert rotated is False
    mock_client.rotate_robot_secret.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_noop_when_robot_missing(monkeypatch):
    """If the build-pusher robot doesn't exist yet (init_harbor hasn't run),
    reconcile_harbor_robot is a no-op — harbor_init creates the robot on
    backend startup."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")
    from app.reconciler import harbor_rotate

    mock_client = MagicMock()
    mock_client.get_robot = AsyncMock(return_value=None)
    mock_client.rotate_robot_secret = AsyncMock()

    with patch("app.reconciler.harbor_rotate.HarborClient", return_value=mock_client):
        rotated = await harbor_rotate.reconcile_harbor_robot()

    assert rotated is False
    mock_client.rotate_robot_secret.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_noop_when_harbor_password_missing(monkeypatch):
    """If `HARBOR_ADMIN_PASSWORD` is unset (test envs without Harbor), the
    rotation is a no-op — neither HarborClient nor the K8s Secret writer
    is touched."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "")
    from app.reconciler import harbor_rotate

    with (
        patch(
            "app.reconciler.harbor_rotate.HarborClient",
            side_effect=AssertionError("HarborClient should not be constructed"),
        ),
        patch(
            "app.reconciler.harbor_rotate._write_docker_config_secret",
            side_effect=AssertionError("Secret writer should not be invoked"),
        ),
    ):
        rotated = await harbor_rotate.reconcile_harbor_robot()

    assert rotated is False


@pytest.mark.asyncio
async def test_reconcile_rotate_api_failure_increments_counter(monkeypatch):
    """When the Harbor rotate-secret API call raises (e.g. 5xx, network
    error), the `harbor_robot_rotate_api` BACKEND_ERRORS stage is
    incremented and the exception propagates to the reconciler loop's
    outer except (`reconcile_harbor_robot` stage)."""
    from prometheus_client import REGISTRY

    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")
    from app.reconciler import harbor_rotate

    def _get(stage: str) -> float:
        return (
            REGISTRY.get_sample_value("lolday_backend_errors_total", {"stage": stage})
            or 0.0
        )

    before = _get("harbor_robot_rotate_api")

    mock_client = MagicMock()
    mock_client.get_robot = AsyncMock(
        return_value={
            "id": 11,
            "name": "robot$build-pusher",
            "duration": -1,
            "expires_at": -1,
        }
    )
    mock_client.update_robot_duration = AsyncMock()
    mock_client.rotate_robot_secret = AsyncMock(
        side_effect=RuntimeError("synthetic Harbor 5xx")
    )

    with (
        patch("app.reconciler.harbor_rotate.HarborClient", return_value=mock_client),
        patch(
            "app.reconciler.harbor_rotate._write_docker_config_secret",
            side_effect=AssertionError("Secret writer should not be reached"),
        ),
        pytest.raises(RuntimeError, match="synthetic Harbor 5xx"),
    ):
        await harbor_rotate.reconcile_harbor_robot()

    assert _get("harbor_robot_rotate_api") == before + 1.0


@pytest.mark.asyncio
async def test_reconcile_secret_write_failure_increments_counter(monkeypatch):
    """When the in-cluster `harbor-push-cred` Secret write fails after a
    successful Harbor rotate, the `harbor_robot_rotate_k8s` BACKEND_ERRORS
    stage is incremented and the exception propagates. The next reconciler
    tick retries from the now-rotated Harbor state."""
    from prometheus_client import REGISTRY

    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")
    from app.reconciler import harbor_rotate

    def _get(stage: str) -> float:
        return (
            REGISTRY.get_sample_value("lolday_backend_errors_total", {"stage": stage})
            or 0.0
        )

    before = _get("harbor_robot_rotate_k8s")

    mock_client = MagicMock()
    mock_client.get_robot = AsyncMock(
        return_value={
            "id": 11,
            "name": "robot$build-pusher",
            "duration": -1,
            "expires_at": -1,
        }
    )
    mock_client.update_robot_duration = AsyncMock()
    mock_client.rotate_robot_secret = AsyncMock(return_value="fresh-secret")

    failing_writer = AsyncMock(side_effect=RuntimeError("synthetic K8s 403"))

    with (
        patch("app.reconciler.harbor_rotate.HarborClient", return_value=mock_client),
        patch(
            "app.reconciler.harbor_rotate._write_docker_config_secret",
            failing_writer,
        ),
        pytest.raises(RuntimeError, match="synthetic K8s 403"),
    ):
        await harbor_rotate.reconcile_harbor_robot()

    failing_writer.assert_awaited_once_with("robot$build-pusher", "fresh-secret")
    assert _get("harbor_robot_rotate_k8s") == before + 1.0
