"""Phase 11b: render Hydra YAML config + separate CSV files for the detector container.

Replaces the Phase 4 JSON renderer. Hydra YAML is composable (user
overrides via ``+key.sub=value``), and decoupling dataset CSVs from the
config makes the contract portable to non-Kubernetes runners — a local
``maldet run`` call only needs the CSV files placed next to the YAML.

The detector reads ``/mnt/config/config.yaml`` and
``/mnt/config/{train,test,predict}.csv``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

import yaml


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
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            dst[k] = _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def _unflatten(params: dict[str, Any]) -> dict[str, Any]:
    """Turn ``{"model.n_estimators": 500}`` into ``{"model": {"n_estimators": 500}}``.

    Raises :class:`ValueError` if the input mixes a flat dict-valued key
    (``"model": {...}``) with a dotted key sharing the same prefix
    (``"model.n_estimators"``). Intent is ambiguous in that case and
    silently letting one path win has produced subtle hyperparameter bugs.
    """
    prefixes: dict[str, set[str]] = {}
    for raw_key in params:
        if "." in raw_key:
            prefix = raw_key.split(".", 1)[0]
            prefixes.setdefault(prefix, set()).add(raw_key)

    for prefix, dotted_keys in prefixes.items():
        if prefix in params and isinstance(params[prefix], dict):
            raise ValueError(
                f"user_params mixes flat-dict {prefix!r}={params[prefix]!r} "
                f"with dotted keys {sorted(dotted_keys)!r}; pick one style"
            )

    out: dict[str, Any] = {}
    for raw_key, val in params.items():
        if "." not in raw_key:
            if (
                raw_key in out
                and isinstance(out[raw_key], dict)
                and isinstance(val, dict)
            ):
                out[raw_key] = _deep_merge(out[raw_key], val)
            else:
                out[raw_key] = val
            continue
        parts = raw_key.split(".")
        cursor = out
        for p in parts[:-1]:
            if p not in cursor or not isinstance(cursor[p], dict):
                cursor[p] = {}
            cursor = cursor[p]
        cursor[parts[-1]] = val
    return out


@dataclass(frozen=True)
class JobConfigRenderer:
    samples_root: str
    config_mount: str
    output_mount: str
    source_model_mount: str

    def render_config_yaml(
        self,
        *,
        stage: str,
        user_params: dict[str, Any],
        mlflow_tracking_uri: str,
        mlflow_run_id: str | None,
        mlflow_experiment_id: str | None,
    ) -> str:
        base: dict[str, Any] = {
            "defaults": ["_self_"],
            "stage": stage,
            "paths": {
                "config_dir": self.config_mount,
                "output_dir": self.output_mount,
                "samples_root": self.samples_root,
                "source_model": self.source_model_mount,
            },
            "data": {
                "train_csv": f"{self.config_mount}/train.csv",
                "test_csv": f"{self.config_mount}/test.csv",
                "predict_csv": f"{self.config_mount}/predict.csv",
            },
            "mlflow": {
                "tracking_uri": mlflow_tracking_uri or None,
                "run_id": mlflow_run_id,
                "experiment_id": mlflow_experiment_id,
            },
        }
        nested = _unflatten(copy.deepcopy(user_params))
        merged = _deep_merge(base, nested)
        return yaml.safe_dump(merged, sort_keys=False, default_flow_style=False)

    def render_csv_files(
        self,
        *,
        train_csv: str | None,
        test_csv: str | None,
        predict_csv: str | None,
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        if train_csv is not None:
            out["train.csv"] = train_csv
        if test_csv is not None:
            out["test.csv"] = test_csv
        if predict_csv is not None:
            out["predict.csv"] = predict_csv
        return out


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
