"""K8s Job manifest generator for detector train/eval/predict jobs.

Contract:
- Detector image's Dockerfile sets ENTRYPOINT to the per-detector CLI
  (e.g., `upxelfdet`). We override `command` with just the CLI binary so we
  can pass the action as `args` (this neutralizes the image's original
  ENTRYPOINT+CMD interplay and gives us explicit control).
- Standard mount paths match JobConfigRenderer in job_config.py.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

from app.config import settings
from app.models.job import JobType

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
    detector_cli_command: str,
    action: str,
    mlflow_tracking_uri: str,
    mlflow_run_id: str,
    mlflow_experiment_id: str,
    model_name: str,
) -> dict[str, Any]:
    return {
        "name": "detector",
        "image": detector_image,
        "imagePullPolicy": "IfNotPresent",
        "command": [detector_cli_command],
        "args": [action, "--config", "/mnt/config/config.json"],
        "env": [
            {"name": "MLFLOW_TRACKING_URI", "value": mlflow_tracking_uri},
            {"name": "MLFLOW_RUN_ID", "value": mlflow_run_id},
            {"name": "MLFLOW_EXPERIMENT_ID", "value": mlflow_experiment_id},
            {"name": "MLFLOW_MODEL_NAME", "value": model_name},
            {"name": "TMPDIR", "value": "/tmp"},
            {"name": "HOME", "value": "/tmp"},
        ],
        "volumeMounts": [
            {"name": "config", "mountPath": "/mnt/config", "readOnly": True},
            {"name": "output", "mountPath": "/mnt/output"},
            {"name": "source-model", "mountPath": "/mnt/source-model", "readOnly": True},
            {"name": "samples", "mountPath": "/mnt/samples", "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
        "resources": {
            "requests": {"cpu": "2", "memory": "4Gi"},
            "limits": {
                "cpu": "4",
                "memory": "16Gi",
                "nvidia.com/gpu": 1,
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


def build_job_manifest(
    *,
    job_id: uuid.UUID,
    job_type: JobType,
    detector_image: str,
    detector_cli_command: str,
    mlflow_experiment_id: str,
    mlflow_run_id: str,
    mlflow_tracking_uri: str,
    source_run_id: str | None,
    source_artifact_path: str | None,
    model_name: str = "",
) -> dict[str, Any]:
    """Render a full K8s Job manifest as a Python dict."""

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

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": settings.JOB_NAMESPACE,
            "labels": pod_labels,
        },
        "spec": {
            "activeDeadlineSeconds": _active_deadline(job_type),
            "ttlSecondsAfterFinished": settings.JOB_TTL_SECONDS_AFTER_FINISHED,
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "nodeSelector": {
                        "kubernetes.io/hostname": settings.JOB_NODE_SELECTOR_HOSTNAME
                    },
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
                            detector_cli_command=detector_cli_command,
                            action=job_type.value,
                            mlflow_tracking_uri=mlflow_tracking_uri,
                            mlflow_run_id=mlflow_run_id,
                            mlflow_experiment_id=mlflow_experiment_id,
                            model_name=model_name,
                        )
                    ],
                },
            },
        },
    }
