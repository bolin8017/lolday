"""K8s Job manifest generator for detector train/eval/predict jobs.

Phase 11b contract:
- Detector image's Dockerfile sets ENTRYPOINT to the `maldet` CLI (scaffold
  WORKDIR /app holds the per-detector ``maldet.toml`` manifest). The Volcano
  Job runs ``maldet run <stage> --config /mnt/config/config.yaml``.
- An event-tailer sidecar tails ``/mnt/output/events.jsonl`` and POSTs each
  line to the backend's internal events endpoint.
- Standard mount paths match JobConfigRenderer in job_config.py.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

from app.config import settings
from app.models.job import RESOURCE_PROFILE_GPU_COUNT, JobType, ResourceProfile

POD_LABEL_NAME = "lolday-job"


def job_name(job_type: JobType, job_id: uuid.UUID) -> str:
    """K8s Job name: `job-{type}-{id[:8]}`.

    Kubernetes object names must be ≤ 63 chars DNS-1123.
    """
    return f"job-{job_type.value}-{job_id.hex[:8]}"


def _active_deadline(job_type: JobType) -> int:
    return {
        JobType.TRAIN: settings.JOB_ACTIVE_DEADLINE_TRAIN_SECONDS,
        JobType.EVALUATE: settings.JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS,
        JobType.PREDICT: settings.JOB_ACTIVE_DEADLINE_PREDICT_SECONDS,
    }[job_type]


def _job_token_secret_name(job_id: uuid.UUID) -> str:
    return f"job-token-{job_id.hex[:16]}"


def build_job_token_secret(job_id: uuid.UUID, raw_token: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": _job_token_secret_name(job_id),
            "namespace": settings.JOB_NAMESPACE,
            "labels": {
                "app.kubernetes.io/name": POD_LABEL_NAME,
                "lolday.job-id": str(job_id),
            },
        },
        "type": "Opaque",
        "data": {
            "token": base64.b64encode(raw_token.encode("utf-8")).decode("ascii"),
        },
    }


def _config_writer_init(job_id: uuid.UUID) -> dict[str, Any]:
    return {
        "name": "config-writer",
        "image": settings.JOB_HELPER_IMAGE,
        "imagePullPolicy": "IfNotPresent",
        "command": ["python", "-m", "job_helper.write_config"],
        "env": [
            {"name": "JOB_ID", "value": str(job_id)},
            {"name": "BACKEND_URL", "value": settings.JOB_BACKEND_URL},
            {
                "name": "JOB_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": _job_token_secret_name(job_id),
                        "key": "token",
                    }
                },
            },
        ],
        "volumeMounts": [
            {"name": "config", "mountPath": "/mnt/config"},
        ],
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        },
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }


def _model_fetcher_init(
    mlflow_tracking_uri: str,
    source_run_id: str,
    source_artifact_path: str,
) -> dict[str, Any]:
    return {
        "name": "model-fetcher",
        "image": settings.JOB_HELPER_IMAGE,
        "imagePullPolicy": "IfNotPresent",
        "command": ["python", "-m", "job_helper.fetch_model"],
        "env": [
            {"name": "MLFLOW_TRACKING_URI", "value": mlflow_tracking_uri},
            {"name": "SOURCE_RUN_ID", "value": source_run_id},
            {"name": "ARTIFACT_PATH", "value": source_artifact_path},
            {"name": "TARGET_DIR", "value": "/mnt/source-model"},
        ],
        "volumeMounts": [
            {"name": "source-model", "mountPath": "/mnt/source-model"},
        ],
        "resources": {
            "requests": {"cpu": "100m", "memory": "256Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }


def _detector_container(
    detector_image: str,
    action: str,
    mlflow_tracking_uri: str,
    mlflow_run_id: str,
    mlflow_experiment_id: str,
    gpu_count: int,
    gpu_strategy: str,
) -> dict[str, Any]:
    return {
        "name": "detector",
        "image": detector_image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["maldet"],
        "args": ["run", action, "--config", "/mnt/config/config.yaml"],
        "env": [
            {"name": "MLFLOW_TRACKING_URI", "value": mlflow_tracking_uri},
            {"name": "MLFLOW_RUN_ID", "value": mlflow_run_id},
            {"name": "MLFLOW_EXPERIMENT_ID", "value": mlflow_experiment_id},
            {"name": "MALDET_MANIFEST", "value": "/app/maldet.toml"},
            {"name": "MALDET_GPU_COUNT", "value": str(gpu_count)},
            {"name": "MALDET_DISTRIBUTED_STRATEGY", "value": gpu_strategy},
            {"name": "TMPDIR", "value": "/tmp"},
            {"name": "HOME", "value": "/tmp"},
            # ``USER`` short-circuits ``getpass.getuser()`` so it doesn't fall
            # through to ``pwd.getpwuid(os.getuid())`` — UID 1000 has no
            # ``/etc/passwd`` entry under our ``runAsUser`` security context,
            # and torch>=2.x calls ``getuser()`` at import time via
            # ``torch._dynamo.cache_dir`` → process exits with ``KeyError``
            # before the detector ever runs.
            {"name": "USER", "value": "maldet"},
        ],
        "volumeMounts": [
            {"name": "config", "mountPath": "/mnt/config", "readOnly": True},
            {"name": "output", "mountPath": "/mnt/output"},
            {
                "name": "source-model",
                "mountPath": "/mnt/source-model",
                "readOnly": True,
            },
            {"name": "samples", "mountPath": "/mnt/samples", "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
        "resources": {
            "requests": {"cpu": "2", "memory": "4Gi"},
            "limits": {
                "cpu": "4",
                "memory": "16Gi",
                "nvidia.com/gpu": gpu_count,
            },
        },
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }


def _event_tailer_sidecar(
    job_id: uuid.UUID, internal_events_url: str
) -> dict[str, Any]:
    return {
        "name": "event-tailer",
        "image": settings.JOB_HELPER_IMAGE,
        "imagePullPolicy": "IfNotPresent",
        "command": ["python", "-m", "job_helper.tail_events"],
        "args": ["/mnt/output/events.jsonl"],
        "env": [
            {"name": "INTERNAL_EVENTS_URL", "value": internal_events_url},
            {
                "name": "JOB_TOKEN",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": _job_token_secret_name(job_id),
                        "key": "token",
                    }
                },
            },
        ],
        "volumeMounts": [
            {"name": "output", "mountPath": "/mnt/output"},
        ],
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "200m", "memory": "128Mi"},
        },
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }


def build_volcano_job_manifest(
    *,
    job_id: uuid.UUID,
    job_type: JobType,
    detector_image: str,
    mlflow_experiment_id: str,
    mlflow_run_id: str,
    mlflow_tracking_uri: str,
    source_run_id: str | None,
    source_artifact_path: str | None,
    internal_events_url: str,
    queue_name: str,
    resource_profile: ResourceProfile = ResourceProfile.STANDARD,
    gpu_strategy: str = "ddp",
) -> dict[str, Any]:
    """Render a ``batch.volcano.sh/v1alpha1`` Job manifest as a Python dict.

    Routes all training jobs through Volcano scheduler against the
    ``lolday-training`` Queue. Gang scheduling is trivially satisfied for
    single-pod jobs (replicas=1, minAvailable=1). Builds use a separate
    ``batch/v1`` Job path in services/build.py — they do not need queueing.

    Task-level policies translate the Phase 4 ``backoffLimit: 0`` semantics:
    ``PodFailed → AbortJob`` marks the whole Volcano Job failed on any pod
    failure, matching the old no-automatic-retry behaviour.
    """

    name = job_name(job_type, job_id)
    pod_labels = {
        "app.kubernetes.io/name": POD_LABEL_NAME,
        "lolday.job-id": str(job_id),
        "lolday.job-type": job_type.value,
    }

    init_containers = [_config_writer_init(job_id)]
    needs_source_model = job_type in (JobType.EVALUATE, JobType.PREDICT)
    if needs_source_model:
        if not source_run_id:
            raise ValueError("source_run_id required for evaluate/predict jobs")
        init_containers.append(
            _model_fetcher_init(
                mlflow_tracking_uri=mlflow_tracking_uri,
                source_run_id=source_run_id,
                source_artifact_path=source_artifact_path or "model",
            )
        )

    volumes = [
        {
            "name": "samples",
            "persistentVolumeClaim": {"claimName": "samples", "readOnly": True},
        },
        {"name": "config", "emptyDir": {"sizeLimit": "32Mi"}},
        {"name": "output", "emptyDir": {"sizeLimit": "10Gi"}},
        {"name": "source-model", "emptyDir": {"sizeLimit": "2Gi"}},
        {"name": "tmp", "emptyDir": {"sizeLimit": "1Gi", "medium": "Memory"}},
    ]

    gpu_count = RESOURCE_PROFILE_GPU_COUNT[resource_profile]

    pod_spec = {
        "activeDeadlineSeconds": _active_deadline(job_type),
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "nodeSelector": {"kubernetes.io/hostname": settings.JOB_NODE_SELECTOR_HOSTNAME},
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 1000,
            "fsGroup": 1000,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumes": volumes,
        "initContainers": init_containers,
        "containers": [
            _detector_container(
                detector_image=detector_image,
                action=job_type.value,
                mlflow_tracking_uri=mlflow_tracking_uri,
                mlflow_run_id=mlflow_run_id,
                mlflow_experiment_id=mlflow_experiment_id,
                gpu_count=gpu_count,
                gpu_strategy=gpu_strategy,
            ),
            _event_tailer_sidecar(job_id, internal_events_url),
        ],
    }

    return {
        "apiVersion": "batch.volcano.sh/v1alpha1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": settings.JOB_NAMESPACE,
            "labels": pod_labels,
        },
        "spec": {
            "schedulerName": "volcano",
            "minAvailable": 1,
            "queue": queue_name,
            "ttlSecondsAfterFinished": settings.JOB_TTL_SECONDS_AFTER_FINISHED,
            "tasks": [
                {
                    "name": "main",
                    "replicas": 1,
                    "policies": [
                        {"event": "TaskCompleted", "action": "CompleteJob"},
                        {"event": "PodFailed", "action": "AbortJob"},
                    ],
                    "template": {
                        "metadata": {"labels": pod_labels},
                        "spec": pod_spec,
                    },
                }
            ],
        },
    }
