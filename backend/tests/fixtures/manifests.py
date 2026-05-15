"""Shared manifest constants for integration tests.

Extracted from ``backend/tests/conftest.py`` (R1 slim, T10) so the root
conftest stays focused on foundational fixtures only.

Import path::

    from tests.fixtures.manifests import _MINIMAL_MANIFEST, RICH_MANIFEST_WITH_TRAIN_DEFAULTS
"""

_MINIMAL_MANIFEST = {
    "detector": {"name": "upxelfdet", "version": "0.4.0", "framework": "sklearn"},
    "input": {
        "binary_format": "elf",
        "required_sections": [],
        "dataset_contract": "sample_csv",
    },
    "output": {
        "task": "binary_classification",
        "classes": ["Benign", "Malware"],
        "positive_class": "Malware",
        "score_range": [0.0, 1.0],
    },
    "resources": {
        "supports": ["cpu", "gpu2"],
        "recommended": "cpu",
        "min_memory_gib": 2,
        "gpu_required": False,
    },
    "lifecycle": {
        "stages": ["train", "evaluate", "predict"],
        "supports_serving": False,
        "supports_hpsweep": True,
        "supports_distributed": False,
        "supports_multinode": False,
    },
    "artifacts": {
        "model": {"path": "model/", "type": "dir"},
        "metrics": {"path": "metrics.json", "type": "file"},
        "predictions": {"path": "predictions.csv", "type": "file"},
    },
    "compat": {"min_python": "3.12", "min_maldet": "1.0", "schema_version": 1},
    "stages": {
        # Phase 11e: each stage carries config_class + params_schema. The default
        # schema here is intentionally permissive (no ``additionalProperties:
        # false``) so integration tests that submit arbitrary user params still
        # pass; jsonschema-rejection cases live in
        # ``tests/test_jsonschema_validate_params.py``.
        "train": {
            "config_class": "test.configs:TrainConfig",
            "params_schema": {"type": "object"},
        },
        "evaluate": {
            "config_class": "test.configs:EvaluateConfig",
            "params_schema": {"type": "object"},
        },
        "predict": {
            "config_class": "test.configs:PredictConfig",
            "params_schema": {"type": "object"},
        },
    },
}


# Phase 13b Q1: train-stage manifest mirroring elfrfdet — a params_schema with
# per-field ``default`` values, including ``max_depth: None`` to lock down the
# "default declared as null" round-trip. Reused by the detector_defaults
# round-trip tests in ``test_routers_jobs.py``.
RICH_MANIFEST_WITH_TRAIN_DEFAULTS = {
    **_MINIMAL_MANIFEST,
    "stages": {
        **_MINIMAL_MANIFEST["stages"],
        "train": {
            "config_class": "test.configs:TrainConfig",
            "params_schema": {
                "type": "object",
                "properties": {
                    "n_estimators": {"type": "integer", "default": 100},
                    "max_depth": {"type": ["integer", "null"], "default": None},
                    "random_state": {"type": "integer", "default": 42},
                },
            },
        },
    },
}
