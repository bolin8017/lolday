"""Tests for app.services.gpu_signal — host-aware GPU state via DCGM/Prom."""

import contextlib
import inspect
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
    """Patch the module-level singleton _HTTP_CLIENT used by _query_prometheus."""
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
    """Regression: _HTTP_CLIENT must exist at module level (not per-call).

    See ``test_query_prometheus_reuses_module_client`` for the leak context.
    """
    assert isinstance(gpu_signal._HTTP_CLIENT, httpx.Client)


def test_query_prometheus_reuses_module_client(monkeypatch):
    """The module-level Client must be reused — no per-call construction.

    The probe runs 10 iterations of ``_query_prometheus``; if any iteration
    creates a fresh ``httpx.Client``, the counter increments and the assertion
    fails. Catches a regression to the pre-2026-05-12 per-call pattern that
    leaked ~2 MiB/iter of glibc arena pages (full context in spec
    ``docs/superpowers/specs/2026-05-12-backend-httpx-client-leak-fix-design.md``).
    The inner ``try/except PrometheusUnavailable`` keeps the failure message
    on a regression clean: if someone reverts to ``with httpx.Client(...)``
    the fresh Client's ``.get()`` is not the mocked singleton, so it would
    DNS-fail on the real URL — we want the assertion to fail with the
    construction-count message, not a DNS error.
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

    # On regression, the reverted code creates a fresh real Client which
    # tries to hit the real Prom URL; the HTTP failure is incidental — the
    # construction-count assertion below is what catches the regression.
    for _ in range(10):
        with contextlib.suppress(gpu_signal.PrometheusUnavailable):
            gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")

    assert construction_count == 0, (
        f"_query_prometheus must reuse the module-level Client; saw "
        f"{construction_count} new Client constructions across 10 calls."
    )


def test_query_prometheus_after_close_raises_unavailable(monkeypatch):
    """After close_http_client(), _query_prometheus raises PrometheusUnavailable.

    Without the explicit None-guard a late-arriving FIFO tick during
    lifespan teardown would AttributeError on ``None.get(...)`` and bypass
    the fail-safe path that ``compute_real_gpu_state`` relies on.
    """
    monkeypatch.setattr(gpu_signal, "_HTTP_CLIENT", None)
    with pytest.raises(gpu_signal.PrometheusUnavailable, match="already closed"):
        gpu_signal._query_prometheus("DCGM_FI_DEV_GPU_UTIL")


def test_close_http_client_is_idempotent(monkeypatch):
    """Calling close_http_client() twice must not raise.

    Lifespan teardown does not guard against double-close; a regression
    that drops the ``if _HTTP_CLIENT is None: return`` early-exit would
    AttributeError on the second call.
    """
    fake_client = MagicMock(spec=httpx.Client)
    monkeypatch.setattr(gpu_signal, "_HTTP_CLIENT", fake_client)

    gpu_signal.close_http_client()
    fake_client.close.assert_called_once()
    assert gpu_signal._HTTP_CLIENT is None

    gpu_signal.close_http_client()  # second call must be a no-op
    fake_client.close.assert_called_once()  # still only one underlying close


def test_close_http_client_swallows_close_exceptions(monkeypatch):
    """close_http_client() must not propagate exceptions from .close().

    Underlying socket close can raise OSError / BrokenPipeError on a
    weird transport state; lifespan teardown propagating those would
    abort shutdown hygiene for unrelated tasks.  The post-close invariant
    (``_HTTP_CLIENT is None``) must still hold so subsequent
    ``_query_prometheus`` calls take the closed-client guard path
    rather than calling .get() on a half-closed Client.
    """
    fake_client = MagicMock(spec=httpx.Client)
    fake_client.close.side_effect = OSError("socket already closed")
    monkeypatch.setattr(gpu_signal, "_HTTP_CLIENT", fake_client)

    gpu_signal.close_http_client()  # must not raise

    assert gpu_signal._HTTP_CLIENT is None, (
        "post-close invariant violated: _HTTP_CLIENT must be None even when "
        "the underlying close() raised"
    )
    fake_client.close.assert_called_once()


def test_lifespan_teardown_calls_close_http_client():
    """The FastAPI lifespan must invoke gpu_signal.close_http_client().

    Source-level assertion (not a TestClient run) because the real
    lifespan does Alembic head checks + Harbor init that need
    infrastructure not available in unit tests.  The check guards
    against a refactor that drops the close-wiring line.
    """
    from app.main import lifespan

    src = inspect.getsource(lifespan)
    assert "close_http_client" in src, (
        "lifespan source must reference close_http_client; the leak-fix "
        "wiring has regressed"
    )
    yield_pos = src.find("yield")
    close_pos = src.find("close_http_client")
    assert close_pos > yield_pos, (
        "close_http_client must be in the lifespan teardown (after yield), "
        "not in startup"
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
