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

from app.config import settings

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
