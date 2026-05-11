"""job_spec (phase 11b): detector container runs `maldet run`; sidecar tails events.

Ports the Phase 4/8 regression coverage from the deleted
``test_services_job_spec.py`` so deterministic naming, Volcano kind/queue
wiring, secret shape, sample mounts, and the GPU-count-per-profile map
stay tested against the new signature.
"""

from __future__ import annotations

import base64
import uuid

from app.models.job import JobType, ResourceProfile
from app.services.job_spec import (
    build_job_token_secret,
    build_volcano_job_manifest,
    job_name,
)


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
        queue_name="lolday-u-test",
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
        c
        for c in m["spec"]["tasks"][0]["template"]["spec"]["containers"]
        if c["name"] == "event-tailer"
    )
    env = {e["name"]: e for e in sidecar["env"]}
    assert env["INTERNAL_EVENTS_URL"]["value"].endswith("/events")
    assert env["JOB_TOKEN"]["valueFrom"]["secretKeyRef"]["key"] == "token"


def test_detector_environment_injects_gpu_strategy_standard() -> None:
    # Phase 3 — STANDARD profile (0 GPU) overrides the caller's DDP strategy
    # to "none" because DDP across 0 GPUs is a config error; the override
    # protects detectors that fail loudly on the mismatch. Caller-supplied
    # gpu_strategy is preserved only when gpu_count >= 2 (multi-GPU).
    m = _build(resource_profile=ResourceProfile.STANDARD, gpu_strategy="ddp")
    container = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["MALDET_GPU_COUNT"] == "0"
    assert env["MALDET_DISTRIBUTED_STRATEGY"] == "none"


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


def test_detector_container_has_user_env_for_torch_getuser() -> None:
    """``USER`` short-circuits ``getpass.getuser()`` so torch>=2.x doesn't fall
    back to ``pwd.getpwuid(uid)`` and crash — UID 1000 has no /etc/passwd entry
    under our security context. Phase 11d regression."""
    m = _build(resource_profile=ResourceProfile.GPU2, gpu_strategy="ddp")
    container = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env.get("USER")  # any non-empty value is fine


def test_detector_container_has_system_metrics_env() -> None:
    """MLflow 2.8+ system metrics logging is enabled via env vars; spec § 5.6."""
    m = _build()
    container = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING"] == "true"
    assert env["MLFLOW_SYSTEM_METRICS_SAMPLING_INTERVAL"] == "10"


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
        queue_name="lolday-u-test",
        gpu_strategy="ddp",
    )
    init_names = [
        c["name"] for c in m["spec"]["tasks"][0]["template"]["spec"]["initContainers"]
    ]
    assert "model-fetcher" in init_names


# ---------------------------------------------------------------------------
# Ports from the pre-Phase-11b test_services_job_spec.py — shape of the
# generated manifest is load-bearing for Volcano scheduling, label selectors,
# and per-profile GPU allocation.
# ---------------------------------------------------------------------------


def test_job_name_deterministic_and_short() -> None:
    jid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert job_name(JobType.TRAIN, jid) == "job-train-00000000"
    assert len(job_name(JobType.TRAIN, jid)) <= 63


def test_job_name_differs_per_type() -> None:
    jid = uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert job_name(JobType.TRAIN, jid) != job_name(JobType.EVALUATE, jid)
    assert job_name(JobType.EVALUATE, jid) != job_name(JobType.PREDICT, jid)


def test_build_job_token_secret_contains_raw_token() -> None:
    """Secret stores the job token base64-encoded (opaque, not hashed).

    Downstream sidecar containers consume the raw token as-is.
    """
    jid = uuid.uuid4()
    raw_token = "raw-abc123"
    secret = build_job_token_secret(jid, raw_token)
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == f"job-token-{jid.hex[:16]}"
    decoded = base64.b64decode(secret["data"]["token"]).decode()
    assert decoded == raw_token


def test_manifest_is_volcano_kind() -> None:
    m = _build()
    assert m["apiVersion"] == "batch.volcano.sh/v1alpha1"
    assert m["kind"] == "Job"


def test_spec_has_queue_and_scheduler() -> None:
    m = _build()
    # Phase 2 — queue is now per-user (passed in by the caller); _build
    # passes the test sentinel "lolday-u-test" so the manifest reflects it.
    # The fallback "lolday-training" queue still exists in the chart for
    # non-user code paths; that's covered by the chart-level helm test.
    assert m["spec"]["queue"] == "lolday-u-test"
    assert m["spec"]["schedulerName"] == "volcano"
    # minAvailable=1 is the gang-scheduling no-op for a single-pod job.
    assert m["spec"]["minAvailable"] == 1


def test_gpu1_profile_emits_strategy_none() -> None:
    """Phase 3 — 1 GPU → MALDET_DISTRIBUTED_STRATEGY=none (DDP no-op on single GPU)."""
    m = _build(resource_profile=ResourceProfile.GPU1)
    detector = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in detector["env"]}
    assert env["MALDET_GPU_COUNT"] == "1"
    assert env["MALDET_DISTRIBUTED_STRATEGY"] == "none"
    limits = detector["resources"]["limits"]
    assert limits["nvidia.com/gpu"] == 1


def test_gpu2_profile_keeps_explicit_strategy() -> None:
    """Phase 3 sanity — 2 GPU keeps the caller's gpu_strategy (default ddp)."""
    m = _build(resource_profile=ResourceProfile.GPU2, gpu_strategy="ddp")
    detector = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in detector["env"]}
    assert env["MALDET_GPU_COUNT"] == "2"
    assert env["MALDET_DISTRIBUTED_STRATEGY"] == "ddp"


def test_active_deadline_override_passes_through() -> None:
    """Phase 5 — per-job override populates spec.template.spec.activeDeadlineSeconds."""
    m = build_volcano_job_manifest(
        job_id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        job_type=JobType.TRAIN,
        detector_image="x",
        mlflow_experiment_id="e1",
        mlflow_run_id="r1",
        mlflow_tracking_uri="x",
        source_run_id=None,
        source_artifact_path=None,
        internal_events_url="x",
        queue_name="lolday-u-test",
        active_deadline_seconds=43200,
    )
    pod_spec = m["spec"]["tasks"][0]["template"]["spec"]
    assert pod_spec["activeDeadlineSeconds"] == 43200


def test_active_deadline_default_unchanged_when_no_override() -> None:
    """Phase 5 — None override → existing per-type default (train = 6h)."""
    m = _build()  # default = no override
    pod_spec = m["spec"]["tasks"][0]["template"]["spec"]
    assert pod_spec["activeDeadlineSeconds"] == 21600  # 6h train default


def test_spec_has_exactly_one_task() -> None:
    m = _build()
    tasks = m["spec"]["tasks"]
    assert len(tasks) == 1
    main_task = tasks[0]
    assert main_task["name"] == "main"
    assert main_task["replicas"] == 1


def test_standard_profile_requests_zero_gpu() -> None:
    """Phase 8 renamed STANDARD to mean CPU (0 GPUs). GPU2 is the only
    GPU-bearing profile today; any new one must update the map."""
    m = _build(resource_profile=ResourceProfile.STANDARD)
    detector = next(
        c
        for c in m["spec"]["tasks"][0]["template"]["spec"]["containers"]
        if c["name"] == "detector"
    )
    assert detector["resources"]["limits"]["nvidia.com/gpu"] == 0


def test_gpu2_profile_requests_two_gpus() -> None:
    m = _build(resource_profile=ResourceProfile.GPU2)
    detector = next(
        c
        for c in m["spec"]["tasks"][0]["template"]["spec"]["containers"]
        if c["name"] == "detector"
    )
    assert detector["resources"]["limits"]["nvidia.com/gpu"] == 2


def test_manifest_has_samples_mounts_readonly() -> None:
    m = _build()
    detector = next(
        c
        for c in m["spec"]["tasks"][0]["template"]["spec"]["containers"]
        if c["name"] == "detector"
    )
    mounts = {vm["name"]: vm for vm in detector["volumeMounts"]}
    assert mounts["samples"]["readOnly"] is True
    assert mounts["samples"]["mountPath"] == "/mnt/samples"


def test_manifest_pod_labels_for_network_policy() -> None:
    """The lolday-job-egress NetworkPolicy selects pods by
    ``app.kubernetes.io/name=lolday-job``; the label must live on the task
    template, not just on the Volcano Job metadata.
    """
    m = _build()
    pod_labels = m["spec"]["tasks"][0]["template"]["metadata"]["labels"]
    assert pod_labels["app.kubernetes.io/name"] == "lolday-job"
    assert pod_labels["lolday.job-id"] == "12345678-1234-5678-1234-567812345678"
    assert pod_labels["lolday.job-type"] == "train"
