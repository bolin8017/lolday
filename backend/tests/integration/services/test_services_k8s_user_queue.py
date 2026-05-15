"""Phase 2 — per-user Volcano queue helpers."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from app.services.k8s import ensure_user_queue, queue_name_for_user
from kubernetes.client.exceptions import ApiException


def test_queue_name_for_user_format() -> None:
    uid = uuid.UUID("ab12cd34ef567890abcdef0123456789")
    assert queue_name_for_user(uid) == "lolday-u-ab12cd34ef56"


def test_queue_name_for_user_distinct() -> None:
    a = uuid.uuid4()
    b = uuid.uuid4()
    assert queue_name_for_user(a) != queue_name_for_user(b)


async def test_ensure_user_queue_creates_and_returns_name() -> None:
    uid = uuid.uuid4()
    fake_api = MagicMock()
    with patch("app.services.k8s.volcano_v1alpha1", return_value=fake_api):
        name = await ensure_user_queue(uid)
    assert name == queue_name_for_user(uid)
    fake_api.create_cluster_custom_object.assert_called_once()
    body = fake_api.create_cluster_custom_object.call_args.kwargs["body"]
    assert body["kind"] == "Queue"
    assert body["spec"]["capability"]["nvidia.com/gpu"] == "2"
    assert body["spec"]["capability"]["memory"] == "30Gi"
    assert body["spec"]["weight"] == 1
    assert body["spec"]["reclaimable"] is True
    assert body["metadata"]["labels"]["lolday.io/user-id"] == str(uid)


async def test_ensure_user_queue_409_is_idempotent() -> None:
    uid = uuid.uuid4()
    fake_api = MagicMock()
    fake_api.create_cluster_custom_object.side_effect = ApiException(status=409)
    with patch("app.services.k8s.volcano_v1alpha1", return_value=fake_api):
        # must not raise
        name = await ensure_user_queue(uid)
    assert name == queue_name_for_user(uid)


async def test_ensure_user_queue_other_error_propagates() -> None:
    uid = uuid.uuid4()
    fake_api = MagicMock()
    fake_api.create_cluster_custom_object.side_effect = ApiException(status=500)
    with (
        patch("app.services.k8s.volcano_v1alpha1", return_value=fake_api),
        pytest.raises(ApiException),
    ):
        await ensure_user_queue(uid)
