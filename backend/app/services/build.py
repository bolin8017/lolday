import re
from typing import Any
from uuid import UUID

from app.config import settings

# K8s Job TTL: delete Job 7 days after completion. Matches spec §Build Pipeline ttlSecondsAfterFinished.
# Short TTL: failed build pods keep their EmptyDir volumes (workspace + /tmp,
# 2Gi + 12Gi reserved) on node disk until GC. With 1h we bound the node
# ephemeral-storage pressure from a string of failing builds. Log tails and
# build-failure reasons are persisted in the DB, so 1h is enough lead time
# for a human to `kubectl logs` the pod if they need raw detail.
JOB_TTL_SECONDS = 3600


def _slugify(s: str) -> str:
    """K8s-safe slug (DNS-1123): lowercase alphanum + hyphen, max 63 chars."""
    s = re.sub(r"[^a-z0-9-]", "-", s.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:63]


def build_job_name(detector_name: str, git_tag: str, build_id: UUID) -> str:
    short_id = str(build_id).replace("-", "")[:8]
    return _slugify(f"build-{detector_name}-{git_tag}-{short_id}")


def build_secret_name(build_id: UUID) -> str:
    short_id = str(build_id).replace("-", "")[:8]
    return f"build-git-cred-{short_id}"


def build_git_credential_secret(
    build_id: UUID, username: str, pat_token: str, build_token: str
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": build_secret_name(build_id)},
        "type": "Opaque",
        "stringData": {
            "username": username,
            "token": pat_token,
            "build_token": build_token,
        },
    }


def build_job_spec(
    build_id: UUID,
    detector_name: str,
    git_tag: str,
    owner_repo: str,
) -> dict[str, Any]:
    job_name = build_job_name(detector_name, git_tag, build_id)
    secret_name = build_secret_name(build_id)
    destination = f"{settings.HARBOR_IMAGE_PREFIX}/detectors/{detector_name}:{git_tag}"
    cache_repo = f"{settings.HARBOR_IMAGE_PREFIX}/detectors-cache/{detector_name}"

    base_sc = {
        "allowPrivilegeEscalation": False,
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "capabilities": {"drop": ["ALL"]},
    }
    ro_sc = {**base_sc, "readOnlyRootFilesystem": True}
    # Kaniko needs root to unpack image layers and chown files
    kaniko_sc = {
        "allowPrivilegeEscalation": False,
        "runAsUser": 0,
        "capabilities": {"drop": ["ALL"], "add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"]},
    }

    pod_labels = {"app": "lolday-build", "lolday.io/build-id": str(build_id)}

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "labels": pod_labels},
        "spec": {
            "activeDeadlineSeconds": settings.BUILD_TIMEOUT_SECONDS,
            "ttlSecondsAfterFinished": JOB_TTL_SECONDS,
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsUser": 1000,
                        "fsGroup": 1000,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumes": [
                        {"name": "workspace", "emptyDir": {"sizeLimit": "2Gi"}},
                        # Sized for the heaviest realistic DL detector: a
                        # `uv pip install <repo>` of a torch-dependent detector
                        # pulls torch (~2Gi) plus its full nvidia-cu12 wheel
                        # set (nvidia-cudnn, cublas, cufft, cusolver,
                        # cusparse, nccl, cuda-nvrtc…) totalling ~7Gi
                        # extracted. Plus the venv + uv staging buffer.
                        {"name": "tmp", "emptyDir": {"sizeLimit": "12Gi"}},
                        {
                            "name": "git-cred",
                            "secret": {"secretName": secret_name, "defaultMode": 0o400},
                        },
                        {
                            "name": "harbor-docker-cfg",
                            "secret": {
                                "secretName": "harbor-push-cred",
                                "items": [
                                    {"key": ".dockerconfigjson", "path": "config.json"}
                                ],
                                "defaultMode": 0o400,
                            },
                        },
                    ],
                    "initContainers": [
                        {
                            "name": "clone",
                            "image": settings.BUILD_IMAGE_GIT,
                            "command": ["/bin/sh", "-c"],
                            "args": [
                                "set +x; "
                                "git clone --depth=1 --recurse-submodules "
                                "--branch=\"$GIT_TAG\" "
                                "\"https://$GIT_USER:$GIT_TOKEN@github.com/$REPO.git\" "
                                "/workspace/src && "
                                "git -C /workspace/src rev-parse HEAD > /workspace/git-sha"
                            ],
                            "env": [
                                {"name": "GIT_TAG", "value": git_tag},
                                {"name": "REPO", "value": owner_repo},
                                {
                                    "name": "GIT_USER",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": secret_name, "key": "username"}
                                    },
                                },
                                {
                                    "name": "GIT_TOKEN",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": secret_name, "key": "token"}
                                    },
                                },
                            ],
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": "/workspace"}
                            ],
                            "securityContext": ro_sc,
                            "resources": {
                                "requests": {
                                    "cpu": "100m",
                                    "memory": "128Mi",
                                    "ephemeral-storage": "128Mi",
                                },
                                "limits": {
                                    "cpu": "500m",
                                    "memory": "512Mi",
                                    "ephemeral-storage": "3Gi",
                                },
                            },
                        },
                        {
                            "name": "validate",
                            "image": settings.BUILD_IMAGE_HELPER,
                            "imagePullPolicy": "Always",
                            "command": ["python", "-m", "maldet_validator"],
                            "args": ["/workspace/src"],
                            "env": [
                                {"name": "BUILD_ID", "value": str(build_id)},
                                {
                                    "name": "BUILD_TOKEN",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": secret_name, "key": "build_token"}
                                    },
                                },
                                {"name": "BACKEND_URL", "value": settings.BACKEND_INTERNAL_URL},
                            ],
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": "/workspace"},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                            "securityContext": base_sc,
                            "resources": {
                                "requests": {
                                    "cpu": "200m",
                                    "memory": "256Mi",
                                    # Validator now runs with --no-deps and
                                    # extracts the config schema via importlib
                                    # on a single file — peak /tmp usage is
                                    # well under 256Mi. Request stays modest.
                                    "ephemeral-storage": "256Mi",
                                },
                                # RSS upper bound: even though the --no-deps
                                # validator never loads torch, leave the 8Gi
                                # headroom in case a detector author's config
                                # module accidentally imports something heavy.
                                "limits": {
                                    "cpu": "1",
                                    "memory": "8Gi",
                                    # /tmp EmptyDir has a 12Gi sizeLimit; the
                                    # limit here matches so kubelet evicts the
                                    # container (not the whole node) if the
                                    # validator runs away. Prevents the stale
                                    # /tmp usage from the old design
                                    # triggering node-level eviction.
                                    "ephemeral-storage": "14Gi",
                                },
                            },
                        },
                    ],
                    "containers": [
                        {
                            "name": "kaniko",
                            "image": settings.BUILD_IMAGE_KANIKO,
                            "args": [
                                "--context=dir:///workspace/src",
                                "--dockerfile=Dockerfile",
                                f"--destination={destination}",
                                f"--insecure-registry={settings.HARBOR_IMAGE_PREFIX}",
                                "--cache=true",
                                f"--cache-repo={cache_repo}",
                                "--cache-ttl=336h",
                                "--snapshot-mode=redo",
                                "--log-format=json",
                                "--verbosity=info",
                            ],
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": "/workspace", "readOnly": True},
                                {"name": "harbor-docker-cfg", "mountPath": "/kaniko/.docker", "readOnly": True},
                            ],
                            "securityContext": kaniko_sc,
                            "resources": {
                                "requests": {
                                    "cpu": "1",
                                    "memory": "2Gi",
                                    # Reserve enough ephemeral-storage that
                                    # kaniko is never the first-to-evict when
                                    # the node comes under disk pressure. DL
                                    # image builds unpack ~7Gi of torch +
                                    # nvidia-cu12 wheels into kaniko's own
                                    # filesystem during build.
                                    "ephemeral-storage": "4Gi",
                                },
                                # Kaniko loads the full post-RUN filesystem
                                # into memory to snapshot each layer. For DL
                                # detectors a single RUN that installs torch
                                # + nvidia cu12 wheels leaves ~5Gi unpacked
                                # site-packages — snapshot peaks at ~14Gi
                                # (filesystem copy + layer diff). 12Gi OOMs.
                                "limits": {
                                    "cpu": "2",
                                    "memory": "20Gi",
                                    "ephemeral-storage": "16Gi",
                                },
                            },
                        }
                    ],
                },
            },
        },
    }
