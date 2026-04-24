"""job_spec (phase 11b): detector container runs `maldet run`; sidecar tails events."""

from __future__ import annotations

import uuid

from app.models.job import JobType, ResourceProfile
from app.services.job_spec import build_volcano_job_manifest


def _build(resource_profile=ResourceProfile.STANDARD, gpu_strategy="ddp"):
    return build_volcano_job_manifest(
        job_id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        job_type=JobType.TRAIN,
        detector_image="harbor/lolday/elfrfdet:v2.0.0",
        mlflow_experiment_id="e1",
        mlflow_run_id="r1",
        mlflow_tracking_uri="http://mlflow:5000",
        source_run_id=None,
        source_artifact_path=None,
        resource_profile=resource_profile,
        internal_events_url="http://backend:8000/internal/jobs/12345678-1234-5678-1234-567812345678/events",
        gpu_strategy=gpu_strategy,
    )


def test_detector_command_is_maldet_run() -> None:
    m = _build()
    container = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    assert container["name"] == "detector"
    assert container["command"] == ["maldet"]
    assert container["args"] == ["run", "train", "--config", "/mnt/config/config.yaml"]


def test_has_event_tailer_sidecar() -> None:
    m = _build()
    containers = m["spec"]["tasks"][0]["template"]["spec"]["containers"]
    assert len(containers) == 2
    sidecar = next(c for c in containers if c["name"] == "event-tailer")
    mount_names = {mount["name"] for mount in sidecar["volumeMounts"]}
    assert "output" in mount_names


def test_sidecar_reads_internal_events_url_and_token() -> None:
    m = _build()
    sidecar = next(
        c for c in m["spec"]["tasks"][0]["template"]["spec"]["containers"]
        if c["name"] == "event-tailer"
    )
    env = {e["name"]: e for e in sidecar["env"]}
    assert env["INTERNAL_EVENTS_URL"]["value"].endswith("/events")
    assert env["JOB_TOKEN"]["valueFrom"]["secretKeyRef"]["key"] == "token"


def test_detector_environment_injects_gpu_strategy_standard() -> None:
    m = _build(resource_profile=ResourceProfile.STANDARD, gpu_strategy="ddp")
    container = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["MALDET_GPU_COUNT"] == "0"
    assert env["MALDET_DISTRIBUTED_STRATEGY"] == "ddp"


def test_detector_environment_injects_gpu2() -> None:
    m = _build(resource_profile=ResourceProfile.GPU2, gpu_strategy="ddp")
    container = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["MALDET_GPU_COUNT"] == "2"
    resources_limits = container["resources"]["limits"]
    assert resources_limits["nvidia.com/gpu"] == 2


def test_detector_container_has_maldet_manifest_env() -> None:
    """Tell maldet to find manifest at /app/maldet.toml (scaffold WORKDIR)."""
    m = _build()
    container = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["MALDET_MANIFEST"] == "/app/maldet.toml"


def test_model_fetcher_init_on_evaluate() -> None:
    """Evaluate job must include model-fetcher init container."""
    m = build_volcano_job_manifest(
        job_id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        job_type=JobType.EVALUATE,
        detector_image="harbor/lolday/elfrfdet:v2.0.0",
        mlflow_experiment_id="e1",
        mlflow_run_id="r1",
        mlflow_tracking_uri="http://mlflow:5000",
        source_run_id="src-run-id",
        source_artifact_path="model",
        resource_profile=ResourceProfile.STANDARD,
        internal_events_url="http://backend:8000/internal/jobs/.../events",
        gpu_strategy="ddp",
    )
    init_names = [c["name"] for c in m["spec"]["tasks"][0]["template"]["spec"]["initContainers"]]
    assert "model-fetcher" in init_names
