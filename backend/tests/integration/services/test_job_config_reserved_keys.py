"""H-5: reserved top-level key rejection in JobConfigRenderer.render_config_yaml."""

from __future__ import annotations

import pytest
from app.services.job_config import JobConfigRenderer


@pytest.fixture
def renderer() -> JobConfigRenderer:
    return JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source",
    )


@pytest.mark.parametrize(
    "reserved_key",
    ["mlflow", "paths", "data", "defaults", "lolday", "stage"],
)
def test_render_rejects_reserved_top_level_key(
    renderer: JobConfigRenderer, reserved_key: str
) -> None:
    with pytest.raises(ValueError, match="reserved"):
        renderer.render_config_yaml(
            stage="train",
            user_params={reserved_key: {"x": 1}},
            mlflow_tracking_uri="http://internal-mlflow:5000",
            mlflow_run_id=None,
            mlflow_experiment_id=None,
        )


@pytest.mark.parametrize(
    "dotted_reserved",
    ["mlflow.tracking_uri", "paths.samples_root", "lolday.user_id"],
)
def test_render_rejects_reserved_dotted_key(
    renderer: JobConfigRenderer, dotted_reserved: str
) -> None:
    with pytest.raises(ValueError, match="reserved"):
        renderer.render_config_yaml(
            stage="train",
            user_params={dotted_reserved: "evil"},
            mlflow_tracking_uri="http://internal-mlflow:5000",
            mlflow_run_id=None,
            mlflow_experiment_id=None,
        )


def test_render_accepts_unreserved_keys(renderer: JobConfigRenderer) -> None:
    out = renderer.render_config_yaml(
        stage="train",
        user_params={"model.n_estimators": 500, "training.batch_size": 32},
        mlflow_tracking_uri="http://internal-mlflow:5000",
        mlflow_run_id=None,
        mlflow_experiment_id=None,
    )
    assert "n_estimators: 500" in out
    assert "tracking_uri: http://internal-mlflow:5000" in out
