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


@pytest.fixture
def mock_http_client(monkeypatch):
    """Replace the module-level httpx.Client with a MagicMock for the test.

    After the 2026-05-12 leak fix `_query_prometheus` reuses a module-level
    `_HTTP_CLIENT` instead of constructing a fresh one per call. Tests that
    used to patch `app.services.gpu_signal.httpx.Client` now patch the
    singleton directly.
    """
    mock = MagicMock(spec=httpx.Client)
    monkeypatch.setattr(gpu_signal, "_HTTP_CLIENT", mock)
    return mock


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


def test_query_prometheus_parses_instant_vector(mock_http_client):
    mock_response = MagicMock()
    mock_response.json.return_value = _prom_response(
        [
            {"metric": {"gpu": "0"}, "value": [1730000000, "87.5"]},
            {"metric": {"gpu": "1"}, "value": [1730000000, "0.1"]},
        ]
    )
    mock_response.raise_for_status.return_value = None
    mock_http_client.get.return_value = mock_response

    samples = gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")

    assert len(samples) == 2
    assert samples[0]["metric"]["gpu"] == "0"
    assert samples[0]["value"] == 87.5


def test_query_prometheus_raises_on_http_error(mock_http_client):
    mock_http_client.get.side_effect = httpx.HTTPError("connection refused")

    with pytest.raises(gpu_signal.PrometheusUnavailable):
        gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")


def test_query_prometheus_raises_on_non_success_status_field(mock_http_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "error", "errorType": "bad_data"}
    mock_response.raise_for_status.return_value = None
    mock_http_client.get.return_value = mock_response

    with pytest.raises(gpu_signal.PrometheusUnavailable):
        gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")


def test_module_level_http_client_exists():
    """Regression for the 5 MiB/min memory leak resolved 2026-05-12.

    Spec: docs/superpowers/specs/2026-05-12-backend-httpx-client-leak-fix-design.md.
    The pre-fix code constructed `with httpx.Client(...)` inside
    `_query_prometheus` on every call; each construction allocated ~2 MiB of
    glibc arena pages the kernel did not reclaim, producing a linear leak
    under the every-30s fifo_scheduler cadence.
    """
    assert isinstance(gpu_signal._HTTP_CLIENT, httpx.Client)


def test_query_prometheus_reuses_module_client(monkeypatch):
    """The module-level Client must be reused — no per-call construction.

    The probe runs 10 iterations of `_query_prometheus`; if any iteration
    creates a fresh `httpx.Client`, the counter increments and the assertion
    fails. Catches a regression to the pre-fix per-call pattern.
    """
    construction_count = 0
    real_init = httpx.Client.__init__

    def counting_init(self, *args, **kwargs):
        nonlocal construction_count
        construction_count += 1
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", counting_init)

    mock_response = MagicMock()
    mock_response.json.return_value = _prom_response([])
    mock_response.raise_for_status.return_value = None
    monkeypatch.setattr(
        gpu_signal._HTTP_CLIENT, "get", MagicMock(return_value=mock_response)
    )

    for _ in range(10):
        gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")

    assert construction_count == 0, (
        f"_query_prometheus must reuse the module-level Client; saw "
        f"{construction_count} new Client constructions across 10 calls."
    )


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
    # DCGM_FI_DEV_FB_USED is reported in MiB (per dcgm-exporter
    # dcp-metrics-included.csv); 9240 ≈ 9 GiB.
    util = [_sample(0, 87.5, exported_namespace="lolday-jobs")]
    vram = [_sample(0, 9240, exported_namespace="lolday-jobs")]
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
    vram = [_sample(1, 7200)]  # MiB, ≈ 7 GiB
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
        _sample(0, 9240, exported_namespace="lolday-jobs"),  # MiB
        _sample(1, 7200),
    ]
    k8s = [_sample(0, 87.5, exported_namespace="lolday-jobs")]
    with _patch_queries(util, vram, k8s), _override_settings(2):
        st = gpu_signal.compute_real_gpu_state()
    assert st.free_count == 0
    assert st.in_use_by_lolday_count == 1
    assert st.in_use_by_external_count == 1


def test_state_threshold_below_util_and_vram_means_idle():
    # util 3% < 5% AND vram 200 MiB < 500 MiB -> not "in use"
    util = [_sample(0, 3.0)]
    vram = [_sample(0, 200)]
    k8s: list[dict] = []
    with _patch_queries(util, vram, k8s), _override_settings(2):
        st = gpu_signal.compute_real_gpu_state()
    assert st.free_count == 2
    assert st.in_use_by_external_count == 0


def test_state_high_vram_alone_counts_as_in_use():
    # util 1% (idle) but vram 8192 MiB (= 8 GiB) -> still "in use" (someone
    # has a process holding VRAM, e.g. model loaded but no batch running)
    util = [_sample(0, 1.0)]
    vram = [_sample(0, 8192)]
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
    vram = [_sample(0, 9240, exported_namespace="lolday-jobs"), _sample(2, 1024)]
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


def test_metric_set_to_one_on_fail_safe():
    """When Prom is unreachable, fail-safe metric must be 1."""
    from app.metrics import GPU_SIGNAL_FAIL_SAFE_ACTIVE

    with (
        patch(
            "app.services.gpu_signal._query_prometheus",
            side_effect=gpu_signal.PrometheusUnavailable("simulated"),
        ),
        _override_settings(2),
    ):
        gpu_signal.compute_real_gpu_state()

    assert GPU_SIGNAL_FAIL_SAFE_ACTIVE._value.get() == 1.0


def test_metric_set_to_zero_on_success():
    """When Prom returns cleanly, fail-safe metric must be 0."""
    from app.metrics import GPU_SIGNAL_FAIL_SAFE_ACTIVE

    # Pre-set to 1 to verify it actually transitions
    GPU_SIGNAL_FAIL_SAFE_ACTIVE.set(1)

    with _patch_queries([], [], []), _override_settings(2):
        gpu_signal.compute_real_gpu_state()

    assert GPU_SIGNAL_FAIL_SAFE_ACTIVE._value.get() == 0.0


def test_metric_value_updates_when_state_transitions():
    """Sequential calls must reflect each call's outcome."""
    from app.metrics import GPU_SIGNAL_FAIL_SAFE_ACTIVE

    # Round 1: success -> 0
    with _patch_queries([], [], []), _override_settings(2):
        gpu_signal.compute_real_gpu_state()
    assert GPU_SIGNAL_FAIL_SAFE_ACTIVE._value.get() == 0.0

    # Round 2: fail-safe -> 1
    # Clear cache so the second call actually executes (TTL cache would
    # otherwise return the Round-1 hit without touching the Gauge).
    gpu_signal._gpu_signal_cache.clear()
    with (
        patch(
            "app.services.gpu_signal._query_prometheus",
            side_effect=gpu_signal.PrometheusUnavailable("simulated"),
        ),
        _override_settings(2),
    ):
        gpu_signal.compute_real_gpu_state()
    assert GPU_SIGNAL_FAIL_SAFE_ACTIVE._value.get() == 1.0
