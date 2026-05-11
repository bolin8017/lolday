"""JobConfigRenderer (phase 11b): Hydra YAML + CSV renderer."""

from __future__ import annotations

import pytest
import yaml
from app.services.job_config import JobConfigRenderer


def _make_renderer() -> JobConfigRenderer:
    return JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )


def test_render_train_yaml_shape() -> None:
    renderer = _make_renderer()
    cfg = renderer.render_config_yaml(
        stage="train",
        user_params={"model": {"n_estimators": 500}},
        mlflow_tracking_uri="http://mlflow:5000",
        mlflow_run_id="r123",
        mlflow_experiment_id="e9",
    )
    doc = yaml.safe_load(cfg)
    assert doc["stage"] == "train"
    assert doc["paths"]["config_dir"] == "/mnt/config"
    assert doc["paths"]["samples_root"] == "/mnt/samples"
    assert doc["paths"]["output_dir"] == "/mnt/output"
    assert doc["model"]["n_estimators"] == 500
    assert doc["mlflow"]["tracking_uri"] == "http://mlflow:5000"
    assert doc["mlflow"]["run_id"] == "r123"


def test_render_evaluate_uses_source_model_path() -> None:
    renderer = _make_renderer()
    cfg = renderer.render_config_yaml(
        stage="evaluate",
        user_params={},
        mlflow_tracking_uri="",
        mlflow_run_id=None,
        mlflow_experiment_id=None,
    )
    doc = yaml.safe_load(cfg)
    assert doc["stage"] == "evaluate"
    assert doc["paths"]["source_model"] == "/mnt/source-model"


def test_render_csv_files_returns_dict_of_named_files() -> None:
    renderer = _make_renderer()
    files = renderer.render_csv_files(
        train_csv="file_name,label\nabc,Malware\n",
        test_csv=None,
        predict_csv=None,
    )
    assert files == {"train.csv": "file_name,label\nabc,Malware\n"}


def test_render_csv_files_includes_all_non_null() -> None:
    renderer = _make_renderer()
    files = renderer.render_csv_files(
        train_csv="t",
        test_csv="te",
        predict_csv="p",
    )
    assert set(files.keys()) == {"train.csv", "test.csv", "predict.csv"}


def test_overrides_flatten_nested_params() -> None:
    renderer = _make_renderer()
    cfg = renderer.render_config_yaml(
        stage="train",
        user_params={"model.n_estimators": 500, "trainer.n_jobs": 4},
        mlflow_tracking_uri="",
        mlflow_run_id=None,
        mlflow_experiment_id=None,
    )
    doc = yaml.safe_load(cfg)
    assert doc["model"]["n_estimators"] == 500
    assert doc["trainer"]["n_jobs"] == 4


def test_nested_dict_user_params_deep_merged() -> None:
    renderer = _make_renderer()
    cfg = renderer.render_config_yaml(
        stage="train",
        user_params={"model": {"n_estimators": 100, "max_depth": 5}},
        mlflow_tracking_uri="",
        mlflow_run_id=None,
        mlflow_experiment_id=None,
    )
    doc = yaml.safe_load(cfg)
    assert doc["model"]["n_estimators"] == 100
    assert doc["model"]["max_depth"] == 5


def test_unflatten_rejects_flat_dict_conflicting_with_dotted_key() -> None:
    """Mixing ``model: {...}`` and ``model.x=...`` in the same dict is ambiguous
    — one will silently overwrite the other depending on iteration order.
    Reject the submission rather than guess the user's intent."""
    renderer = _make_renderer()
    with pytest.raises(ValueError, match="mixes flat-dict"):
        renderer.render_config_yaml(
            stage="train",
            user_params={"model": {"n_estimators": 100}, "model.max_depth": 5},
            mlflow_tracking_uri="",
            mlflow_run_id=None,
            mlflow_experiment_id=None,
        )


def test_render_config_yaml_includes_lolday_meta_when_provided() -> None:
    """Spec § 5.4 / § 6.5 — platform-injected lolday.* keys land in the YAML."""
    renderer = _make_renderer()
    yaml_text = renderer.render_config_yaml(
        stage="train",
        user_params={},
        mlflow_tracking_uri="http://m",
        mlflow_run_id="run-1",
        mlflow_experiment_id="42",
        lolday_meta={
            "train_dataset_id": "abc-123",
            "job_id": "job-9",
        },
    )
    assert "lolday:" in yaml_text
    assert "train_dataset_id: abc-123" in yaml_text
    assert "job_id: job-9" in yaml_text


def test_render_config_yaml_empty_lolday_block_when_no_meta() -> None:
    renderer = _make_renderer()
    yaml_text = renderer.render_config_yaml(
        stage="train",
        user_params={},
        mlflow_tracking_uri="http://m",
        mlflow_run_id="run-1",
        mlflow_experiment_id="42",
    )
    assert "lolday: {}" in yaml_text
