"""Job config rendering + idempotency utilities.

`resolved_config` is the exact JSON that will be written to /mnt/config/config.json
inside the detector container. This module is the single source of truth for that
shape.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any


def compute_idempotency_key(
    *,
    user_id: str,
    detector_version_id: str,
    job_type: str,
    train_ds: str | None,
    test_ds: str | None,
    predict_ds: str | None,
    source_model: str | None,
    params: dict[str, Any],
) -> str:
    """Deterministic SHA256 over all submission identity inputs.

    Dict ordering is normalized via json.dumps(sort_keys=True) so param key
    order doesn't produce different keys.
    """
    payload = {
        "user": user_id,
        "dv": detector_version_id,
        "type": job_type,
        "train_ds": train_ds,
        "test_ds": test_ds,
        "predict_ds": predict_ds,
        "source_model": source_model,
        "params": params,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _deep_merge(dst: dict, src: dict) -> dict:
    """Recursive merge: dicts merge, non-dict values from src override dst."""
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            dst[k] = _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


@dataclass(frozen=True)
class JobConfigRenderer:
    """Encapsulates the mount-path contract between backend and job pod.

    Paths are documented in spec §Job Pod Specification.
    """

    samples_root: str
    config_mount: str
    output_mount: str
    source_model_mount: str

    def render(
        self,
        *,
        job_type: str,
        detector_defaults: dict[str, Any],
        user_params: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = copy.deepcopy(detector_defaults)
        cfg = _deep_merge(cfg, copy.deepcopy(user_params))

        cfg.setdefault("data", {})
        cfg.setdefault("output", {})

        cfg["data"]["dataset"] = self.samples_root
        cfg["data"]["train"] = f"{self.config_mount}/train.csv"
        cfg["data"]["test"] = f"{self.config_mount}/test.csv"
        cfg["data"]["predict"] = f"{self.config_mount}/predict.csv"

        cfg["output"]["feature"] = f"{self.output_mount}/features"
        cfg["output"]["vectorize"] = f"{self.output_mount}/vectorize"
        cfg["output"]["prediction"] = f"{self.output_mount}/predictions.csv"
        cfg["output"]["log"] = f"{self.output_mount}/logs"

        if job_type == "train":
            cfg["output"]["model"] = f"{self.output_mount}/model"
        elif job_type in ("evaluate", "predict"):
            cfg["output"]["model"] = self.source_model_mount
        else:
            raise ValueError(f"unknown job_type: {job_type}")

        return cfg


def resolve_source_model_path(source_uri: str) -> str:
    """Given an MLflow artifact URI like 'runs:/<run_id>/model', return the
    artifact sub-path ('model'). Handles nested paths like 'runs:/<id>/model/sub'."""
    if not source_uri.startswith("runs:/"):
        raise ValueError(f"expected runs:/ URI, got {source_uri!r}")
    _, _, rest = source_uri.partition("runs:/")
    parts = rest.split("/", 1)
    if len(parts) < 2:
        return ""
    return parts[1]
