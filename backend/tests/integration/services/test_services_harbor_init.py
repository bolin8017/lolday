"""Tests for ``app.services.harbor_init`` — Harbor startup orchestrator.

The HarborClient REST helpers each have their own respx tests in
``test_services_harbor.py``; this file covers the orchestration: project
loop fan-out, fresh-vs-existing robot branch, docker-config Secret
replace→create fallback on 404, and the BACKEND_ERRORS stage labels for
each swallow-and-continue catch block.
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.metrics import BACKEND_ERRORS
from app.services import harbor_init
from kubernetes.client import ApiException


def _err(stage: str) -> float:
    return BACKEND_ERRORS.labels(stage=stage)._value.get()


@pytest.mark.asyncio
async def test_init_harbor_skips_when_password_unset(monkeypatch):
    """Test boots without HARBOR_ADMIN_PASSWORD; the call must no-op silently."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "")
    # HarborClient must NOT be constructed; assert by patching it to a sentinel.
    with patch(
        "app.services.harbor_init.HarborClient",
        side_effect=AssertionError("HarborClient must not be constructed"),
    ):
        await harbor_init.init_harbor()


@pytest.mark.asyncio
async def test_init_harbor_happy_path_calls_all_projects_and_writes_secret(
    monkeypatch,
):
    """Fresh robot ('secret' key present) — every project ensured, retention
    set on detectors, docker-config Secret written via the k8s API."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    mock_client = MagicMock()
    mock_client.ensure_project = AsyncMock()
    mock_client.ensure_robot_account = AsyncMock(
        return_value={"name": "robot$build-pusher", "secret": "fresh-secret"}
    )
    mock_client.set_retention_policy = AsyncMock()

    write_calls = []

    async def fake_write(name, secret):
        write_calls.append((name, secret))

    with (
        patch("app.services.harbor_init.HarborClient", return_value=mock_client),
        patch(
            "app.services.harbor_init._write_docker_config_secret",
            fake_write,
        ),
    ):
        await harbor_init.init_harbor()

    # Every project ensured exactly once
    assert mock_client.ensure_project.await_count == len(harbor_init.PROJECTS)
    awaited_projects = {
        call.args[0] for call in mock_client.ensure_project.await_args_list
    }
    assert awaited_projects == set(harbor_init.PROJECTS)

    # Robot ensured with the full project list
    mock_client.ensure_robot_account.assert_awaited_once_with(
        harbor_init.ROBOT_NAME, projects=list(harbor_init.PROJECTS)
    )

    # Retention applied to detectors only
    mock_client.set_retention_policy.assert_awaited_once_with(
        "detectors", keep_n_recent=harbor_init.DETECTORS_RETENTION_KEEP_N
    )

    # Docker config Secret persisted with the fresh secret
    assert write_calls == [("robot$build-pusher", "fresh-secret")]


@pytest.mark.asyncio
async def test_init_harbor_existing_robot_skips_secret_write(monkeypatch):
    """Existing robot — Harbor returns no ``secret`` key; the writer must NOT
    be invoked (mirrors the inline `if "secret" in robot:` branch)."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    mock_client = MagicMock()
    mock_client.ensure_project = AsyncMock()
    mock_client.ensure_robot_account = AsyncMock(
        return_value={"name": "robot$build-pusher"}  # no "secret"
    )
    mock_client.set_retention_policy = AsyncMock()

    with (
        patch("app.services.harbor_init.HarborClient", return_value=mock_client),
        patch(
            "app.services.harbor_init._write_docker_config_secret",
            AsyncMock(side_effect=AssertionError("must not be called")),
        ),
    ):
        await harbor_init.init_harbor()


@pytest.mark.asyncio
async def test_init_harbor_swallows_ensure_project_failure(monkeypatch):
    """ensure_project raising must increment BACKEND_ERRORS{stage=ensure_project}
    once per failing project but continue iterating to the next project."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")
    before = _err("ensure_project")

    mock_client = MagicMock()
    # Fail every project to assert the increment fires per iteration.
    mock_client.ensure_project = AsyncMock(side_effect=RuntimeError("harbor down"))
    mock_client.ensure_robot_account = AsyncMock(return_value={"name": "r"})
    mock_client.set_retention_policy = AsyncMock()

    with (
        patch("app.services.harbor_init.HarborClient", return_value=mock_client),
        patch("app.services.harbor_init._write_docker_config_secret", AsyncMock()),
    ):
        await harbor_init.init_harbor()  # must not raise

    after = _err("ensure_project")
    assert after - before == len(harbor_init.PROJECTS)
    # Downstream calls still attempted (swallow-and-continue contract).
    mock_client.ensure_robot_account.assert_awaited_once()
    mock_client.set_retention_policy.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_harbor_swallows_ensure_robot_failure(monkeypatch):
    """ensure_robot_account raising must increment BACKEND_ERRORS{stage=ensure_robot}
    and skip the Secret write, but still attempt set_retention_policy."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")
    before = _err("ensure_robot")

    mock_client = MagicMock()
    mock_client.ensure_project = AsyncMock()
    mock_client.ensure_robot_account = AsyncMock(side_effect=RuntimeError("api 500"))
    mock_client.set_retention_policy = AsyncMock()

    with (
        patch("app.services.harbor_init.HarborClient", return_value=mock_client),
        patch(
            "app.services.harbor_init._write_docker_config_secret",
            AsyncMock(side_effect=AssertionError("must not be called")),
        ),
    ):
        await harbor_init.init_harbor()

    assert _err("ensure_robot") - before == 1
    mock_client.set_retention_policy.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_harbor_swallows_retention_policy_failure(monkeypatch):
    """set_retention_policy raising must increment
    BACKEND_ERRORS{stage=retention_policy} and let the lifespan continue."""
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")
    before = _err("retention_policy")

    mock_client = MagicMock()
    mock_client.ensure_project = AsyncMock()
    mock_client.ensure_robot_account = AsyncMock(return_value={"name": "r"})
    mock_client.set_retention_policy = AsyncMock(
        side_effect=RuntimeError("retention conflict")
    )

    with (
        patch("app.services.harbor_init.HarborClient", return_value=mock_client),
        patch("app.services.harbor_init._write_docker_config_secret", AsyncMock()),
    ):
        await harbor_init.init_harbor()

    assert _err("retention_policy") - before == 1


@pytest.mark.asyncio
async def test_write_docker_config_secret_uses_replace_on_existing(monkeypatch):
    """Happy path: replace_namespaced_secret succeeds; create is not called.

    Also verifies the dockerconfigjson payload shape — registry key matches
    HARBOR_IMAGE_PREFIX, auth blob is base64(robot:secret)."""
    monkeypatch.setattr(
        "app.config.settings.HARBOR_IMAGE_PREFIX", "harbor.lolday.svc/lolday"
    )
    monkeypatch.setattr("app.config.settings.BUILD_NAMESPACE", "lolday-builds")

    core = MagicMock()
    core.replace_namespaced_secret = MagicMock(return_value=None)
    core.create_namespaced_secret = MagicMock(
        side_effect=AssertionError("must not be called on existing")
    )
    monkeypatch.setattr("app.services.harbor_init.core_v1", lambda: core)

    await harbor_init._write_docker_config_secret("robot$build-pusher", "s3cret")

    core.replace_namespaced_secret.assert_called_once()
    kwargs = core.replace_namespaced_secret.call_args.kwargs
    assert kwargs["name"] == "harbor-push-cred"
    assert kwargs["namespace"] == "lolday-builds"
    body = kwargs["body"]
    cfg = json.loads(body.string_data[".dockerconfigjson"])
    assert "harbor.lolday.svc/lolday" in cfg["auths"]
    expected_auth = base64.b64encode(b"robot$build-pusher:s3cret").decode()
    assert cfg["auths"]["harbor.lolday.svc/lolday"]["auth"] == expected_auth


@pytest.mark.asyncio
async def test_write_docker_config_secret_creates_on_404(monkeypatch):
    """First-boot path: replace returns 404 -> fall back to create."""
    monkeypatch.setattr("app.config.settings.BUILD_NAMESPACE", "lolday-builds")

    core = MagicMock()
    core.replace_namespaced_secret = MagicMock(side_effect=ApiException(status=404))
    core.create_namespaced_secret = MagicMock(return_value=None)
    monkeypatch.setattr("app.services.harbor_init.core_v1", lambda: core)

    await harbor_init._write_docker_config_secret("robot$build-pusher", "s3cret")

    core.create_namespaced_secret.assert_called_once()
    kwargs = core.create_namespaced_secret.call_args.kwargs
    assert kwargs["namespace"] == "lolday-builds"


@pytest.mark.asyncio
async def test_write_docker_config_secret_reraises_non_404_apiexception(monkeypatch):
    """A non-404 ApiException (e.g. 403/500) must propagate to the caller so
    the surrounding init_harbor catch block records the failure."""
    core = MagicMock()
    core.replace_namespaced_secret = MagicMock(side_effect=ApiException(status=403))
    core.create_namespaced_secret = MagicMock(
        side_effect=AssertionError("must not fall back on non-404")
    )
    monkeypatch.setattr("app.services.harbor_init.core_v1", lambda: core)

    with pytest.raises(ApiException) as exc:
        await harbor_init._write_docker_config_secret("r", "s")
    assert exc.value.status == 403
