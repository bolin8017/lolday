"""Host-aware GPU state — single source of truth for free-GPU counting.

Reads DCGM exporter metrics through Prometheus to detect both K8s and
non-K8s GPU usage on server30 (a shared lab server).  Used by both the
``/cluster/gpu-status`` UI endpoint and the Phase 6 FIFO scheduler so
they share one signal.

Spec: docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass


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
