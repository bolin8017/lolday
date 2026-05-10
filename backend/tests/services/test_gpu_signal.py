"""Tests for app.services.gpu_signal — host-aware GPU state via DCGM/Prom."""

from unittest.mock import MagicMock, patch

import httpx
from app.services import gpu_signal


def test_module_exposes_dataclasses():
    """The module must expose GPUStatus and GPUState dataclasses."""
    assert hasattr(gpu_signal, "GPUStatus")
    assert hasattr(gpu_signal, "GPUState")


def test_gpustatus_fields():
    s = gpu_signal.GPUStatus(
        gpu_id=0,
        in_use_by_k8s=True,
        in_use_by_external=False,
        util_percent=87.5,
        vram_used_mb=9240,
    )
    assert s.gpu_id == 0
    assert s.in_use_by_k8s is True
    assert s.in_use_by_external is False
    assert s.util_percent == 87.5
    assert s.vram_used_mb == 9240


def test_gpustate_fields():
    s = gpu_signal.GPUState(
        physical_total=2,
        per_gpu=[],
        free_count=2,
        in_use_by_lolday_count=0,
        in_use_by_external_count=0,
        fail_safe_active=False,
        fail_safe_reason=None,
    )
    assert s.physical_total == 2
    assert s.free_count == 2
    assert s.fail_safe_active is False
    assert s.fail_safe_reason is None


def _prom_response(samples: list[dict]) -> dict:
    """Shape of GET /api/v1/query response (Prometheus instant query)."""
    return {
        "status": "success",
        "data": {"resultType": "vector", "result": samples},
    }


@patch("app.services.gpu_signal.httpx.Client")
def test_query_prometheus_parses_instant_vector(mock_client_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = _prom_response(
        [
            {"metric": {"gpu": "0"}, "value": [1730000000, "87.5"]},
            {"metric": {"gpu": "1"}, "value": [1730000000, "0.1"]},
        ]
    )
    mock_response.raise_for_status.return_value = None
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    samples = gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")

    assert len(samples) == 2
    assert samples[0]["metric"]["gpu"] == "0"
    assert samples[0]["value"] == 87.5


@patch("app.services.gpu_signal.httpx.Client")
def test_query_prometheus_raises_on_http_error(mock_client_cls):
    mock_client = MagicMock()
    mock_client.get.side_effect = httpx.HTTPError("connection refused")
    mock_client_cls.return_value.__enter__.return_value = mock_client

    import pytest as _pytest

    with _pytest.raises(gpu_signal.PrometheusUnavailable):
        gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")


@patch("app.services.gpu_signal.httpx.Client")
def test_query_prometheus_raises_on_non_success_status_field(mock_client_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "error", "errorType": "bad_data"}
    mock_response.raise_for_status.return_value = None
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    import pytest as _pytest

    with _pytest.raises(gpu_signal.PrometheusUnavailable):
        gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")
