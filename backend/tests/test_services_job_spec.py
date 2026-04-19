import uuid

import pytest

from app.models.job import JobType
from app.services.job_spec import (
    build_job_manifest,
    build_job_token_secret,
    job_name,
)


def test_job_name_deterministic_and_short():
    jid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert job_name(JobType.TRAIN, jid) == "job-train-00000000"
    assert len(job_name(JobType.TRAIN, jid)) <= 63


def test_job_name_differs_per_type():
    jid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert job_name(JobType.TRAIN, jid) != job_name(JobType.EVALUATE, jid)
    assert job_name(JobType.EVALUATE, jid) != job_name(JobType.PREDICT, jid)


def test_build_job_token_secret_has_hashed_token():
    jid = uuid.uuid4()
    raw_token = "raw-abc123"
    secret = build_job_token_secret(jid, raw_token)
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == f"job-token-{jid.hex[:16]}"
    import base64
    decoded = base64.b64decode(secret["data"]["token"]).decode()
    assert decoded == raw_token


@pytest.fixture
def manifest_args():
    return dict(
        job_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        job_type=JobType.TRAIN,
        detector_image="harbor.harbor.svc:80/detectors/upxelfdet:v0.4.0",
        detector_cli_command="upxelfdet",
        mlflow_experiment_id="42",
        mlflow_run_id="abc123",
        mlflow_tracking_uri="http://mlflow.lolday.svc:5000",
        source_run_id=None,
        source_artifact_path=None,
    )


def test_train_manifest_has_gpu_request_and_correct_args(manifest_args):
    m = build_job_manifest(**manifest_args)
    assert m["kind"] == "Job"
    assert m["spec"]["activeDeadlineSeconds"] == 21600
    assert m["spec"]["backoffLimit"] == 0
    assert m["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    main = next(
        c for c in m["spec"]["template"]["spec"]["containers"] if c["name"] == "detector"
    )
    assert main["image"] == "harbor.harbor.svc:80/detectors/upxelfdet:v0.4.0"
    assert main["command"] == ["upxelfdet"]
    assert main["args"] == ["train", "--config", "/mnt/config/config.json"]
    assert main["resources"]["limits"]["nvidia.com/gpu"] == 1
    assert main["securityContext"]["readOnlyRootFilesystem"] is True
    env_keys = {e["name"] for e in main["env"]}
    assert "MLFLOW_TRACKING_URI" in env_keys
    assert "MLFLOW_RUN_ID" in env_keys


def test_eval_manifest_has_model_fetcher_init(manifest_args):
    args = {**manifest_args, "job_type": JobType.EVALUATE,
            "source_run_id": "xyz789", "source_artifact_path": "model"}
    m = build_job_manifest(**args)
    inits = m["spec"]["template"]["spec"]["initContainers"]
    names = [c["name"] for c in inits]
    assert "config-writer" in names
    assert "model-fetcher" in names
    fetcher = next(c for c in inits if c["name"] == "model-fetcher")
    env_keys = {e["name"] for e in fetcher["env"]}
    assert "SOURCE_RUN_ID" in env_keys


def test_train_manifest_has_no_model_fetcher(manifest_args):
    m = build_job_manifest(**manifest_args)
    inits = m["spec"]["template"]["spec"]["initContainers"]
    names = [c["name"] for c in inits]
    assert "model-fetcher" not in names


def test_predict_manifest_args(manifest_args):
    args = {**manifest_args, "job_type": JobType.PREDICT,
            "source_run_id": "abc", "source_artifact_path": "model"}
    m = build_job_manifest(**args)
    main = next(
        c for c in m["spec"]["template"]["spec"]["containers"] if c["name"] == "detector"
    )
    assert main["args"] == ["predict", "--config", "/mnt/config/config.json"]
    assert m["spec"]["activeDeadlineSeconds"] == 3600


def test_manifest_has_samples_mounts(manifest_args):
    m = build_job_manifest(**manifest_args)
    mounts = {
        vm["name"]: vm for vm in next(
            c for c in m["spec"]["template"]["spec"]["containers"] if c["name"] == "detector"
        )["volumeMounts"]
    }
    assert mounts["samples"]["readOnly"] is True
    assert mounts["samples"]["mountPath"] == "/mnt/samples"


def test_manifest_labels_include_job_id(manifest_args):
    m = build_job_manifest(**manifest_args)
    pod_labels = m["spec"]["template"]["metadata"]["labels"]
    assert pod_labels["app.kubernetes.io/name"] == "lolday-job"
    assert pod_labels["lolday.job-id"] == str(manifest_args["job_id"])
    assert pod_labels["lolday.job-type"] == "train"
