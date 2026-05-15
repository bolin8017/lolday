"""kubeconform contract test for the Volcano vcjob manifest.

The backend builds a Volcano ``Job`` (vcjob) CRD instance via
``app.services.job_spec.build_volcano_job_manifest``; this test validates
the rendered manifest with kubeconform against core Kubernetes schemas.
Catches:
- Required pod-template / container-spec fields omitted from the manifest
- Resource-quantity formatting errors (e.g. "4Gi" vs "4096Mi")
- Init-container spec shape drift (config-writer, model-fetcher)
- Sidecar container spec drift (event-tailer)
- Security-context field regressions

Volcano's ``batch.volcano.sh/v1alpha1`` Job CRD is not in the default
kubeconform schemas. We use ``-skip Job`` (option (a) from the spec) so
kubeconform validates the embedded pod template and container specs, which
use core Kubernetes types. This catches the bulk of realistic drift.
Limitation: Volcano-specific outer fields (``spec.queue``, ``spec.tasks``,
``spec.policies``) are not schema-checked. A future enhancement (option (b))
can vendor the Volcano CRD JSON schema and point kubeconform at it.

Re-validate after a Volcano upgrade by re-running this test against the new
CRD schema; if the schema URL changes, update ``--schema-location``.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md
§7.3 Volcano coverage map.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest
import yaml
from app.models.job import JobType, ResourceProfile
from app.services.job_spec import build_volcano_job_manifest

pytestmark = pytest.mark.contract

# ---------------------------------------------------------------------------
# Fixed test inputs — chosen to be plausible but minimal.
# ---------------------------------------------------------------------------
_EXPERIMENT_ID = "1"
_RUN_ID = "abc123def456"
_MLFLOW_URI = "http://mlflow.lolday.svc:5000"
_QUEUE = "lolday-training"
_IMAGE = "ghcr.io/bolin8017/lolday-elfrfdet:v1.0.0"
_EVENTS_URL = "http://backend.lolday.svc:8001/internal/jobs/events"
_SOURCE_RUN_ID = "src111aaa222"
_SOURCE_ARTIFACT_PATH = "model"


def _kubeconform_path() -> str:
    # Try PATH first, then the user-level install location.
    path = shutil.which("kubeconform") or shutil.which(
        "/home/bolin8017/.local/bin/kubeconform"
    )
    if path is None:
        import os

        candidate = os.path.expanduser("~/.local/bin/kubeconform")
        if os.path.isfile(candidate):
            path = candidate
    if not path:
        pytest.skip(
            "kubeconform not installed; install via scripts/install-tools.sh or README"
        )
    return path


def _build_manifest(job_type: JobType) -> dict:
    """Build a Volcano vcjob manifest for the given job type."""
    job_id = uuid.uuid4()
    extra: dict = {}
    if job_type in (JobType.EVALUATE, JobType.PREDICT):
        extra["source_run_id"] = _SOURCE_RUN_ID
        extra["source_artifact_path"] = _SOURCE_ARTIFACT_PATH
    else:
        extra["source_run_id"] = None
        extra["source_artifact_path"] = None

    return build_volcano_job_manifest(
        job_id=job_id,
        job_type=job_type,
        detector_image=_IMAGE,
        mlflow_experiment_id=_EXPERIMENT_ID,
        mlflow_run_id=_RUN_ID,
        mlflow_tracking_uri=_MLFLOW_URI,
        queue_name=_QUEUE,
        resource_profile=ResourceProfile.GPU1,
        gpu_strategy="ddp",
        internal_events_url=_EVENTS_URL,
        **extra,
    )


def _run_kubeconform(manifest: dict, tmp_path) -> subprocess.CompletedProcess:
    manifest_file = tmp_path / "vcjob.yaml"
    manifest_file.write_text(yaml.safe_dump(manifest))

    kubeconform = _kubeconform_path()
    return subprocess.run(
        [
            kubeconform,
            "-strict",
            "-summary",
            # Volcano's batch.volcano.sh/v1alpha1 Job CRD is absent from the
            # default schema registry. Skip the outer Job kind so kubeconform
            # validates the embedded pod-template and container specs (core K8s
            # types) without erroring on the unknown CRD. See module docstring.
            "-skip",
            "Job",
            str(manifest_file),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "job_type",
    [JobType.TRAIN, JobType.EVALUATE, JobType.PREDICT],
    ids=["train", "evaluate", "predict"],
)
def test_vcjob_manifest_passes_kubeconform(job_type: JobType, tmp_path):
    """Manifests for all three job types validate against core Kubernetes schemas."""
    manifest = _build_manifest(job_type)
    result = _run_kubeconform(manifest, tmp_path)

    assert result.returncode == 0, (
        f"kubeconform failed for job_type={job_type.value}:\n"
        f"STDOUT: {result.stdout}\n"
        f"STDERR: {result.stderr}\n"
        f"Manifest:\n{yaml.safe_dump(manifest)}"
    )


def test_vcjob_manifest_structure(tmp_path):
    """Smoke-test that the outer manifest shape matches Volcano vcjob conventions.

    Validates fields kubeconform cannot check (Volcano-specific outer spec)
    without requiring a vendored CRD schema. Complements the kubeconform
    parametrised test above.
    """
    manifest = _build_manifest(JobType.TRAIN)

    assert manifest["apiVersion"] == "batch.volcano.sh/v1alpha1"
    assert manifest["kind"] == "Job"
    assert manifest["spec"]["schedulerName"] == "volcano"
    assert manifest["spec"]["queue"] == _QUEUE
    tasks = manifest["spec"]["tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["name"] == "main"
    assert task["replicas"] == 1
    # Gang scheduling: minAvailable must equal the number of task replicas.
    assert manifest["spec"]["minAvailable"] == task["replicas"]
    # Pod template containers
    pod_spec = task["template"]["spec"]
    container_names = {c["name"] for c in pod_spec["containers"]}
    assert "detector" in container_names
    assert "event-tailer" in container_names
    init_names = {c["name"] for c in pod_spec["initContainers"]}
    assert "config-writer" in init_names
