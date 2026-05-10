"""Host-aware GPU state — single source of truth for free-GPU counting.

Reads DCGM exporter metrics through Prometheus to detect both K8s and
non-K8s GPU usage on server30 (a shared lab server).  Used by both the
``/cluster/gpu-status`` UI endpoint and the Phase 6 FIFO scheduler so
they share one signal.

Spec: docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from cachetools import TTLCache, cached

from app.config import settings
from app.metrics import BACKEND_ERRORS, GPU_SIGNAL_FAIL_SAFE_ACTIVE

logger = logging.getLogger(__name__)


class PrometheusUnavailable(Exception):
    """Raised when Prom is unreachable, times out, or returns a non-success
    response.  The caller is expected to surface fail-safe behavior."""


@dataclass(frozen=True)
class GPUStatus:
    gpu_id: int
    in_use_by_k8s: bool
    in_use_by_external: bool
    util_percent: float
    vram_used_mb: int


_gpu_signal_cache: TTLCache = TTLCache(
    maxsize=1, ttl=settings.GPU_SIGNAL_CACHE_TTL_SECONDS
)


@dataclass(frozen=True)
class GPUState:
    physical_total: int
    per_gpu: list[GPUStatus]
    free_count: int
    in_use_by_lolday_count: int
    in_use_by_external_count: int
    fail_safe_active: bool
    fail_safe_reason: str | None


def _query_prometheus(query: str) -> list[dict]:
    """Run an instant query against the configured Prometheus server.

    Returns a list of {"metric": {label: value}, "value": float} dicts.
    Raises PrometheusUnavailable on transport, HTTP, or non-"success" body.
    """
    url = f"{settings.GPU_SIGNAL_PROMETHEUS_URL}/api/v1/query"
    timeout = settings.GPU_SIGNAL_QUERY_TIMEOUT_SECONDS
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, params={"query": query})
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as e:
        raise PrometheusUnavailable(f"Prometheus HTTP error: {e}") from e
    except ValueError as e:  # JSONDecodeError is a subclass
        raise PrometheusUnavailable(f"Prometheus returned non-JSON: {e}") from e

    if body.get("status") != "success":
        raise PrometheusUnavailable(
            f"Prometheus query failed: status={body.get('status')!r}"
        )

    out: list[dict] = []
    for sample in body.get("data", {}).get("result", []) or []:
        try:
            metric = sample["metric"]
            value = float(sample["value"][1])
        except (KeyError, IndexError, ValueError, TypeError) as e:
            logger.warning("malformed Prom sample skipped: %r (%s)", sample, e)
            continue
        out.append({"metric": metric, "value": value})
    return out


def _reduce_threshold_samples(
    samples: list[dict],
    threshold: float,
) -> tuple[dict[int, float], set[int]]:
    """Reduce per-GPU samples into (max_value_per_gpu, busy_gpu_ids).

    A GPU is "busy" when any of its samples exceeds ``threshold``.
    Malformed samples (missing/non-int gpu label) are skipped silently —
    `_query_prometheus` already logs them at warning level upstream.
    """
    by_gpu: dict[int, float] = {}
    busy: set[int] = set()
    for s in samples:
        try:
            gpu_id = int(s["metric"]["gpu"])
        except (KeyError, ValueError, TypeError):
            continue
        by_gpu[gpu_id] = max(by_gpu.get(gpu_id, 0.0), s["value"])
        if s["value"] > threshold:
            busy.add(gpu_id)
    return by_gpu, busy


def _gpu_ids_from_samples(samples: list[dict]) -> set[int]:
    """Extract the set of distinct gpu IDs present in a list of Prom samples."""
    out: set[int] = set()
    for s in samples:
        try:
            out.add(int(s["metric"]["gpu"]))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _classify_gpus(
    util_samples: list[dict],
    vram_samples: list[dict],
    k8s_samples: list[dict],
    physical_total: int,
    util_threshold: float,
    vram_threshold_bytes: float,
) -> list[GPUStatus]:
    util_by_gpu, util_busy = _reduce_threshold_samples(util_samples, util_threshold)
    vram_by_gpu, vram_busy = _reduce_threshold_samples(
        vram_samples, vram_threshold_bytes
    )
    k8s_by_gpu = _gpu_ids_from_samples(k8s_samples)

    busy = util_busy | vram_busy

    # Spec §7: detect CLUSTER_PHYSICAL_GPU_COUNT vs actual hardware mismatch
    seen_gpu_ids = set(util_by_gpu) | set(vram_by_gpu) | k8s_by_gpu
    out_of_range = {g for g in seen_gpu_ids if g >= physical_total}
    if out_of_range:
        BACKEND_ERRORS.labels(stage="gpu_signal_count_mismatch").inc()
        logger.warning(
            "DCGM samples reference gpu ids %s beyond CLUSTER_PHYSICAL_GPU_COUNT=%d "
            "— these are silently dropped; check if hardware was upgraded without "
            "bumping the env var",
            sorted(out_of_range),
            physical_total,
        )

    statuses: list[GPUStatus] = []
    for gpu_id in range(physical_total):
        is_active = gpu_id in busy
        is_k8s = gpu_id in k8s_by_gpu
        statuses.append(
            GPUStatus(
                gpu_id=gpu_id,
                in_use_by_k8s=is_k8s,
                in_use_by_external=is_active and not is_k8s,
                util_percent=util_by_gpu.get(gpu_id, 0.0),
                vram_used_mb=int(vram_by_gpu.get(gpu_id, 0.0) / (1024 * 1024)),
            )
        )
    return statuses


@cached(_gpu_signal_cache)
def compute_real_gpu_state() -> GPUState:
    """Single source of truth for host-aware GPU availability.

    Returns a snapshot reflecting both K8s allocations and host-level GPU
    activity.  When Prometheus is unreachable, returns a fail-safe state
    with free_count=0; the caller (FIFO scheduler) decides what to do.
    """
    physical = settings.CLUSTER_PHYSICAL_GPU_COUNT
    util_threshold = settings.GPU_SIGNAL_UTIL_THRESHOLD_PERCENT
    vram_threshold_bytes = settings.GPU_SIGNAL_VRAM_THRESHOLD_MB * 1024 * 1024

    try:
        util_samples = _query_prometheus("DCGM_FI_DEV_GPU_UTIL")
        vram_samples = _query_prometheus("DCGM_FI_DEV_FB_USED")
        k8s_namespace = settings.JOB_NAMESPACE
        k8s_samples = _query_prometheus(
            f'DCGM_FI_DEV_GPU_UTIL{{exported_namespace="{k8s_namespace}"}}'
        )
    except PrometheusUnavailable as e:
        GPU_SIGNAL_FAIL_SAFE_ACTIVE.set(1)
        return GPUState(
            physical_total=physical,
            per_gpu=[],
            free_count=0,
            in_use_by_lolday_count=0,
            in_use_by_external_count=0,
            fail_safe_active=True,
            fail_safe_reason=str(e),
        )

    statuses = _classify_gpus(
        util_samples,
        vram_samples,
        k8s_samples,
        physical_total=physical,
        util_threshold=util_threshold,
        vram_threshold_bytes=vram_threshold_bytes,
    )
    GPU_SIGNAL_FAIL_SAFE_ACTIVE.set(0)
    in_use_by_lolday = sum(1 for s in statuses if s.in_use_by_k8s)
    in_use_by_external = sum(1 for s in statuses if s.in_use_by_external)
    free_count = sum(
        1 for s in statuses if not s.in_use_by_k8s and not s.in_use_by_external
    )
    return GPUState(
        physical_total=physical,
        per_gpu=statuses,
        free_count=free_count,
        in_use_by_lolday_count=in_use_by_lolday,
        in_use_by_external_count=in_use_by_external,
        fail_safe_active=False,
        fail_safe_reason=None,
    )
