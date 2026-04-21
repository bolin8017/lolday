import uuid

import pytest

from app.models.job import JobType, ResourceProfile
from app.services.job_spec import (
    build_job_token_secret,
    build_volcano_job_manifest,
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


# ---------------------------------------------------------------------------
# Volcano Job structure — Phase 7.3 routed all training jobs through Volcano
# scheduler (queue: lolday-training) via batch.volcano.sh/v1alpha1 Job kind.
# Builds still use plain batch/v1 Job (see services/build.py, separate path).
# ---------------------------------------------------------------------------


def test_train_manifest_is_volcano_kind(manifest_args):
    m = build_volcano_job_manifest(**manifest_args)
    assert m["apiVersion"] == "batch.volcano.sh/v1alpha1"
    assert m["kind"] == "Job"


def test_volcano_spec_carries_queue_and_scheduler(manifest_args):
    m = build_volcano_job_manifest(**manifest_args)
    assert m["spec"]["queue"] == "lolday-training"
    assert m["spec"]["schedulerName"] == "volcano"
    # minAvailable=1 is semantically equivalent to "no gang requirement" for
    # single-pod jobs — at least 1 of the tasks' pods must be schedulable.
    assert m["spec"]["minAvailable"] == 1


def test_volcano_spec_has_exactly_one_task(manifest_args):
    m = build_volcano_job_manifest(**manifest_args)
    tasks = m["spec"]["tasks"]
    assert len(tasks) == 1
    main_task = tasks[0]
    assert main_task["replicas"] == 1
    # Task name used by Volcano for pod naming: <job>-<task>-<index>
    assert main_task["name"] == "main"


def test_train_manifest_has_gpu_request_and_correct_args(manifest_args):
    m = build_volcano_job_manifest(**manifest_args)
    task_spec = m["spec"]["tasks"][0]["template"]["spec"]
    assert task_spec["automountServiceAccountToken"] is False
    main = next(c for c in task_spec["containers"] if c["name"] == "detector")
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
    m = build_volcano_job_manifest(**args)
    task_spec = m["spec"]["tasks"][0]["template"]["spec"]
    inits = task_spec["initContainers"]
    names = [c["name"] for c in inits]
    assert "config-writer" in names
    assert "model-fetcher" in names
    fetcher = next(c for c in inits if c["name"] == "model-fetcher")
    env_keys = {e["name"] for e in fetcher["env"]}
    assert "SOURCE_RUN_ID" in env_keys


def test_train_manifest_has_no_model_fetcher(manifest_args):
    m = build_volcano_job_manifest(**manifest_args)
    task_spec = m["spec"]["tasks"][0]["template"]["spec"]
    inits = task_spec["initContainers"]
    names = [c["name"] for c in inits]
    assert "model-fetcher" not in names


def test_predict_manifest_args(manifest_args):
    args = {**manifest_args, "job_type": JobType.PREDICT,
            "source_run_id": "abc", "source_artifact_path": "model"}
    m = build_volcano_job_manifest(**args)
    task_spec = m["spec"]["tasks"][0]["template"]["spec"]
    main = next(c for c in task_spec["containers"] if c["name"] == "detector")
    assert main["args"] == ["predict", "--config", "/mnt/config/config.json"]
    # activeDeadlineSeconds moves to the pod level inside the Volcano task
    # template (Volcano Job spec doesn't have a top-level activeDeadlineSeconds).
    assert task_spec["activeDeadlineSeconds"] == 3600


def test_manifest_has_samples_mounts(manifest_args):
    m = build_volcano_job_manifest(**manifest_args)
    task_spec = m["spec"]["tasks"][0]["template"]["spec"]
    mounts = {
        vm["name"]: vm for vm in next(
            c for c in task_spec["containers"] if c["name"] == "detector"
        )["volumeMounts"]
    }
    assert mounts["samples"]["readOnly"] is True
    assert mounts["samples"]["mountPath"] == "/mnt/samples"


def test_manifest_labels_include_job_id(manifest_args):
    m = build_volcano_job_manifest(**manifest_args)
    # Pod-level labels live on the task's template.metadata, preserved so the
    # lolday-job-egress NetworkPolicy (selects app.kubernetes.io/name=lolday-job)
    # still matches Volcano-scheduled pods.
    pod_labels = m["spec"]["tasks"][0]["template"]["metadata"]["labels"]
    assert pod_labels["app.kubernetes.io/name"] == "lolday-job"
    assert pod_labels["lolday.job-id"] == str(manifest_args["job_id"])
    assert pod_labels["lolday.job-type"] == "train"


def test_standard_profile_requests_one_gpu(manifest_args):
    m = build_volcano_job_manifest(
        **manifest_args, resource_profile=ResourceProfile.STANDARD
    )
    main = next(
        c for c in m["spec"]["tasks"][0]["template"]["spec"]["containers"]
        if c["name"] == "detector"
    )
    assert main["resources"]["limits"]["nvidia.com/gpu"] == 1


def test_gpu2_profile_requests_two_gpus(manifest_args):
    m = build_volcano_job_manifest(
        **manifest_args, resource_profile=ResourceProfile.GPU2
    )
    main = next(
        c for c in m["spec"]["tasks"][0]["template"]["spec"]["containers"]
        if c["name"] == "detector"
    )
    assert main["resources"]["limits"]["nvidia.com/gpu"] == 2


def test_default_profile_is_standard(manifest_args):
    """Omitting resource_profile must keep Phase 7.5 behaviour (1 GPU)."""
    m = build_volcano_job_manifest(**manifest_args)
    main = next(
        c for c in m["spec"]["tasks"][0]["template"]["spec"]["containers"]
        if c["name"] == "detector"
    )
    assert main["resources"]["limits"]["nvidia.com/gpu"] == 1
