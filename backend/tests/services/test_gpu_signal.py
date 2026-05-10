"""Tests for app.services.gpu_signal — host-aware GPU state via DCGM/Prom."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.services import gpu_signal


@pytest.fixture(autouse=True)
def _clear_gpu_signal_cache():
    gpu_signal._gpu_signal_cache.clear()
    yield
    gpu_signal._gpu_signal_cache.clear()


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

    with pytest.raises(gpu_signal.PrometheusUnavailable):
        gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")


@patch("app.services.gpu_signal.httpx.Client")
def test_query_prometheus_raises_on_non_success_status_field(mock_client_cls):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "error", "errorType": "bad_data"}
    mock_response.raise_for_status.return_value = None
    mock_client.get.return_value = mock_response
    mock_client_cls.return_value.__enter__.return_value = mock_client

    with pytest.raises(gpu_signal.PrometheusUnavailable):
        gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")


def _sample(gpu: int, value: float, exported_namespace: str = "") -> dict:
    metric = {"gpu": str(gpu)}
    if exported_namespace:
        metric["exported_namespace"] = exported_namespace
        metric["exported_pod"] = f"job-pod-{gpu}"
    return {"metric": metric, "value": value}


def _patch_queries(util_samples, vram_samples, k8s_samples):
    """Patch _query_prometheus to return three different shapes per call.

    Call order in compute_real_gpu_state():
      1. util query  -> util_samples
      2. vram query  -> vram_samples
      3. k8s query   -> k8s_samples
    """
    return patch(
        "app.services.gpu_signal._query_prometheus",
        side_effect=[util_samples, vram_samples, k8s_samples],
    )


def _override_settings(physical: int = 2):
    return patch.object(gpu_signal.settings, "CLUSTER_PHYSICAL_GPU_COUNT", physical)


def test_state_all_free():
    with (
        _patch_queries([], [], []),
        _override_settings(2),
    ):
        st = gpu_signal.compute_real_gpu_state()
    assert st.free_count == 2
    assert st.in_use_by_lolday_count == 0
    assert st.in_use_by_external_count == 0
    assert st.fail_safe_active is False
    assert [g.gpu_id for g in st.per_gpu] == [0, 1]


def test_state_lolday_on_gpu0_only():
    util = [_sample(0, 87.5, exported_namespace="lolday-jobs")]
    vram = [_sample(0, 9240e6, exported_namespace="lolday-jobs")]
    k8s = [_sample(0, 87.5, exported_namespace="lolday-jobs")]
    with _patch_queries(util, vram, k8s), _override_settings(2):
        st = gpu_signal.compute_real_gpu_state()
    assert st.free_count == 1
    assert st.in_use_by_lolday_count == 1
    assert st.in_use_by_external_count == 0
    assert st.per_gpu[0].in_use_by_k8s is True
    assert st.per_gpu[0].in_use_by_external is False
    assert st.per_gpu[1].in_use_by_k8s is False
    assert st.per_gpu[1].in_use_by_external is False


def test_state_external_on_gpu1_only():
    util = [_sample(1, 54.0)]  # no exported_namespace -> external
    vram = [_sample(1, 7200e6)]
    k8s: list[dict] = []
    with _patch_queries(util, vram, k8s), _override_settings(2):
        st = gpu_signal.compute_real_gpu_state()
    assert st.free_count == 1
    assert st.in_use_by_lolday_count == 0
    assert st.in_use_by_external_count == 1
    assert st.per_gpu[1].in_use_by_external is True
    assert st.per_gpu[1].in_use_by_k8s is False


def test_state_lolday_and_external_mixed():
    util = [
        _sample(0, 87.5, exported_namespace="lolday-jobs"),
        _sample(1, 54.0),
    ]
    vram = [
        _sample(0, 9240e6, exported_namespace="lolday-jobs"),
        _sample(1, 7200e6),
    ]
    k8s = [_sample(0, 87.5, exported_namespace="lolday-jobs")]
    with _patch_queries(util, vram, k8s), _override_settings(2):
        st = gpu_signal.compute_real_gpu_state()
    assert st.free_count == 0
    assert st.in_use_by_lolday_count == 1
    assert st.in_use_by_external_count == 1


def test_state_threshold_below_util_and_vram_means_idle():
    # util 3% < 5% AND vram 200MB < 500MB -> not "in use"
    util = [_sample(0, 3.0)]
    vram = [_sample(0, 200e6)]
    k8s: list[dict] = []
    with _patch_queries(util, vram, k8s), _override_settings(2):
        st = gpu_signal.compute_real_gpu_state()
    assert st.free_count == 2
    assert st.in_use_by_external_count == 0


def test_state_high_vram_alone_counts_as_in_use():
    # util 1% (idle) but vram 8GB -> still "in use" (someone has a process holding VRAM)
    util = [_sample(0, 1.0)]
    vram = [_sample(0, 8 * 1024 * 1024 * 1024)]
    k8s: list[dict] = []
    with _patch_queries(util, vram, k8s), _override_settings(2):
        st = gpu_signal.compute_real_gpu_state()
    assert st.free_count == 1
    assert st.in_use_by_external_count == 1


def test_state_fail_safe_when_prom_unavailable():
    with (
        patch(
            "app.services.gpu_signal._query_prometheus",
            side_effect=gpu_signal.PrometheusUnavailable("simulated"),
        ),
        _override_settings(2),
    ):
        st = gpu_signal.compute_real_gpu_state()
    assert st.fail_safe_active is True
    assert st.free_count == 0
    assert "simulated" in (st.fail_safe_reason or "")
    assert st.per_gpu == []


def test_compute_real_gpu_state_is_cached_within_ttl():
    """Two calls within TTL should issue 0 extra Prom queries (3 total)."""
    gpu_signal._gpu_signal_cache.clear()
    with (
        patch(
            "app.services.gpu_signal._query_prometheus",
            return_value=[],
        ) as mock_q,
        _override_settings(2),
    ):
        st1 = gpu_signal.compute_real_gpu_state()
        st2 = gpu_signal.compute_real_gpu_state()
    assert st1 == st2
    # 3 queries on the first call (util, vram, k8s) + 0 on the second
    assert mock_q.call_count == 3


def test_state_query_uses_configured_job_namespace():
    """Q2 query must use settings.JOB_NAMESPACE, not a hardcoded string."""
    captured: list[str] = []

    def _capture(query: str) -> list[dict]:
        captured.append(query)
        return []

    with (
        patch("app.services.gpu_signal._query_prometheus", side_effect=_capture),
        patch.object(gpu_signal.settings, "JOB_NAMESPACE", "custom-jobs-ns"),
        _override_settings(2),
    ):
        gpu_signal.compute_real_gpu_state()

    # Third query is the K8s-pod filter; must reference the configured namespace
    assert any("custom-jobs-ns" in q for q in captured), captured
    assert all("lolday-jobs" not in q for q in captured), captured


def test_state_count_mismatch_emits_metric_and_warning(caplog):
    """gpu samples with id >= CLUSTER_PHYSICAL_GPU_COUNT must trigger
    BACKEND_ERRORS{stage='gpu_signal_count_mismatch'} + warning log."""
    from app.metrics import BACKEND_ERRORS

    util = [_sample(0, 87.5, exported_namespace="lolday-jobs"), _sample(2, 50.0)]
    vram = [_sample(0, 9240e6, exported_namespace="lolday-jobs"), _sample(2, 1e9)]
    k8s = [_sample(0, 87.5, exported_namespace="lolday-jobs")]

    before = BACKEND_ERRORS.labels(stage="gpu_signal_count_mismatch")._value.get()

    with (
        _patch_queries(util, vram, k8s),
        _override_settings(2),
        caplog.at_level("WARNING"),
    ):
        st = gpu_signal.compute_real_gpu_state()

    after = BACKEND_ERRORS.labels(stage="gpu_signal_count_mismatch")._value.get()
    assert after > before, "metric must increment when out-of-range gpu_id seen"
    assert any(
        "gpu_signal_count_mismatch" not in r.message and "beyond" in r.message
        for r in caplog.records
    ), "warning log must mention beyond-physical-count drop"
    # gpu_id=2 dropped (only 2 physical GPUs); per_gpu still has 0 and 1
    assert len(st.per_gpu) == 2
