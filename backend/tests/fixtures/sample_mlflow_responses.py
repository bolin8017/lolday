"""Canned MLflow REST responses for respx-based tests.

Source of truth: https://mlflow.org/docs/2.20.0/rest-api.html
"""

EXPERIMENT_CREATED = {
    "experiment_id": "42",
}

EXPERIMENT_GET = {
    "experiment": {
        "experiment_id": "42",
        "name": "detector:upxelfdet:v0.4.0",
        "artifact_location": "file:///mlflow-artifacts/42",
        "lifecycle_stage": "active",
    }
}

RUN_CREATED = {
    "run": {
        "info": {
            "run_id": "abc123def456",
            "experiment_id": "42",
            "status": "RUNNING",
            "start_time": 1713350000000,
            "artifact_uri": "file:///mlflow-artifacts/42/abc123def456/artifacts",
        },
        "data": {"metrics": [], "params": [], "tags": []},
    }
}

RUN_FINISHED = {
    "run": {
        "info": {
            "run_id": "abc123def456",
            "experiment_id": "42",
            "status": "FINISHED",
            "start_time": 1713350000000,
            "end_time": 1713351800000,
            "artifact_uri": "file:///mlflow-artifacts/42/abc123def456/artifacts",
        },
        "data": {
            "metrics": [
                {
                    "key": "accuracy",
                    "value": 0.93,
                    "timestamp": 1713351000000,
                    "step": 0,
                },
                {"key": "f1", "value": 0.91, "timestamp": 1713351000000, "step": 0},
            ],
            "params": [
                {"key": "model.type", "value": "SVM"},
                {"key": "vectorize.method", "value": "ngram_numeric"},
            ],
            "tags": [{"key": "maldet.action", "value": "train"}],
        },
    }
}

MODEL_VERSION_CREATED = {
    "model_version": {
        "name": "upxelfdet",
        "version": "1",
        "creation_timestamp": 1713351900000,
        "last_updated_timestamp": 1713351900000,
        "current_stage": "None",
        "source": "runs:/abc123def456/model",
        "run_id": "abc123def456",
        "status": "READY",
    }
}

MODEL_VERSION_TRANSITIONED = {
    "model_version": {
        "name": "upxelfdet",
        "version": "1",
        "current_stage": "Production",
    }
}

REGISTERED_MODELS_SEARCH = {
    "registered_models": [
        {
            "name": "upxelfdet",
            "creation_timestamp": 1713350000000,
            "last_updated_timestamp": 1713352000000,
            "latest_versions": [
                {
                    "version": "1",
                    "current_stage": "Production",
                    "run_id": "abc123def456",
                }
            ],
        }
    ]
}

MODEL_VERSIONS_SEARCH = {
    "model_versions": [
        {
            "name": "upxelfdet",
            "version": "1",
            "current_stage": "Production",
            "run_id": "abc123def456",
        },
    ]
}
