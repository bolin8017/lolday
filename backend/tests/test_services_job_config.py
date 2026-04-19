import pytest

from app.services.job_config import (
    JobConfigRenderer,
    compute_idempotency_key,
    resolve_source_model_path,
)


def test_compute_idempotency_key_stable_across_dict_order():
    p1 = {"model": {"C": 1, "kernel": "rbf"}, "seed": 8017}
    p2 = {"seed": 8017, "model": {"kernel": "rbf", "C": 1}}
    k1 = compute_idempotency_key(
        user_id="u1", detector_version_id="dv1", job_type="train",
        train_ds="td1", test_ds="td2", predict_ds=None, source_model=None, params=p1,
    )
    k2 = compute_idempotency_key(
        user_id="u1", detector_version_id="dv1", job_type="train",
        train_ds="td1", test_ds="td2", predict_ds=None, source_model=None, params=p2,
    )
    assert k1 == k2


def test_compute_idempotency_key_differs_on_params():
    base_args = dict(
        user_id="u1", detector_version_id="dv1", job_type="train",
        train_ds="td1", test_ds="td2", predict_ds=None, source_model=None,
    )
    k1 = compute_idempotency_key(**base_args, params={"C": 1})
    k2 = compute_idempotency_key(**base_args, params={"C": 10})
    assert k1 != k2


def test_render_train_config_injects_standard_paths():
    detector_defaults = {
        "data": {"train": "./train.csv", "test": "./test.csv", "dataset": "./data"},
        "output": {"model": "./model", "feature": "./feat", "vectorize": "./vec",
                   "prediction": "./pred.csv", "log": "./log"},
        "feature": {"section_name": ".block_1"},
        "vectorize": {"method": "ngram_numeric", "ngram_size": 2, "encoding": "TF"},
        "model": {"type": "SVM", "params": {"C": 100, "kernel": "rbf"}},
        "classify": True,
        "seed": 8017,
    }
    user_params = {
        "model": {"params": {"C": 50}},
        "seed": 42,
    }
    r = JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    cfg = r.render(
        job_type="train",
        detector_defaults=detector_defaults,
        user_params=user_params,
    )
    assert cfg["data"]["train"] == "/mnt/config/train.csv"
    assert cfg["data"]["test"] == "/mnt/config/test.csv"
    assert cfg["data"]["dataset"] == "/mnt/samples"
    assert cfg["output"]["model"] == "/mnt/output/model"
    assert cfg["output"]["prediction"] == "/mnt/output/predictions.csv"
    assert cfg["seed"] == 42
    assert cfg["model"]["params"]["C"] == 50
    assert cfg["model"]["params"]["kernel"] == "rbf"
    assert cfg["model"]["type"] == "SVM"


def test_render_eval_config_points_model_at_source():
    detector_defaults = {
        "data": {"test": "./test.csv", "dataset": "./data"},
        "output": {"model": "./model", "prediction": "./pred.csv", "log": "./log"},
    }
    r = JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    cfg = r.render(
        job_type="evaluate",
        detector_defaults=detector_defaults,
        user_params={},
    )
    assert cfg["output"]["model"] == "/mnt/source-model"
    assert cfg["data"]["test"] == "/mnt/config/test.csv"


def test_render_predict_config():
    detector_defaults = {
        "data": {"predict": "./predict.csv", "dataset": "./data"},
        "output": {"model": "./model", "prediction": "./pred.csv", "log": "./log"},
    }
    r = JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    cfg = r.render(
        job_type="predict",
        detector_defaults=detector_defaults,
        user_params={},
    )
    assert cfg["data"]["predict"] == "/mnt/config/predict.csv"
    assert cfg["output"]["model"] == "/mnt/source-model"
    assert cfg["output"]["prediction"] == "/mnt/output/predictions.csv"


def test_resolve_source_model_path():
    assert resolve_source_model_path("runs:/abc/model") == "model"
    assert resolve_source_model_path("runs:/abc123/model/subdir") == "model/subdir"
