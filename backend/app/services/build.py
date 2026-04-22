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
    # Rootless BuildKit matches the upstream moby/buildkit
    # examples/kubernetes/job.rootless.yaml security context: the pod runs
    # as a non-root user, but rootlesskit uses setuid newuidmap/newgidmap
    # to set up the user namespace — those require no_new_privs=false
    # (i.e. allowPrivilegeEscalation defaults to true) and the SETUID /
    # SETGID capabilities in the bounding set. Dropping ALL caps or
    # setting allowPrivilegeEscalation=false breaks rootless startup with
    # `newuidmap ... failed: operation not permitted`. Security comes
    # from the user namespace isolation itself: buildkitd only ever runs
    # as UID 1000 inside the container; setuid is only used transiently
    # during namespace setup.
    buildkit_sc = {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "seccompProfile": {"type": "Unconfined"},
        "appArmorProfile": {"type": "Unconfined"},
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
                        "runAsNonRoot": True,
                        "runAsUser": 1000,
                        "fsGroup": 1000,
                        # Pod-wide default; the buildkit container overrides
                        # to Unconfined for user-namespace creation.
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
                        # BuildKit's overlay snapshotter needs a writable
                        # directory under HOME. Disk-backed (not tmpfs) so
                        # multi-GB DL image layers don't blow RAM.
                        {"name": "buildkit-state", "emptyDir": {"sizeLimit": "30Gi"}},
                        {
                            "name": "git-cred",
                            "secret": {
                                "secretName": secret_name,
                                # 0o440 = u=r, g=r. With fsGroup=1000 the
                                # mounted file is owned root:1000, so the
                                # UID 1000 runAsUser reads via the group bit.
                                "defaultMode": 0o440,
                            },
                        },
                        {
                            "name": "harbor-docker-cfg",
                            "secret": {
                                "secretName": "harbor-push-cred",
                                "items": [
                                    {"key": ".dockerconfigjson", "path": "config.json"}
                                ],
                                # Same group-readable pattern as git-cred.
                                # Required for BuildKit's DOCKER_CONFIG.
                                "defaultMode": 0o440,
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
                            "name": "buildkit",
                            "image": settings.BUILD_IMAGE_BUILDKIT,
                            "imagePullPolicy": "IfNotPresent",
                            # buildctl-daemonless.sh is the upstream wrapper
                            # that forks a local buildkitd and runs buildctl
                            # against its unix socket — same one-pod-per-build
                            # shape that Kaniko had, so the reconciler's Job
                            # lifecycle logic needs no changes.
                            "command": ["/bin/sh", "-c"],
                            "args": [
                                "set -eu; "
                                "export BUILDCTL_CONNECT_RETRIES_ON_STARTUP=10; "
                                "buildctl-daemonless.sh build "
                                "  --frontend dockerfile.v0 "
                                "  --local context=/workspace/src "
                                "  --local dockerfile=/workspace/src "
                                f"  --output type=image,name={destination},push=true,registry.insecure=true "
                                f"  --export-cache type=registry,ref={cache_repo},mode=max,registry.insecure=true "
                                f"  --import-cache type=registry,ref={cache_repo},registry.insecure=true "
                                "  --progress plain"
                            ],
                            "env": [
                                # Required for rootless in Kubernetes. Docker
                                # sets `systempaths=unconfined` via daemon.json;
                                # we don't have that, so explicitly disable the
                                # process sandbox.
                                {"name": "BUILDKITD_FLAGS", "value": "--oci-worker-no-process-sandbox"},
                                # buildctl reads $DOCKER_CONFIG/config.json to
                                # authenticate to the registry; the secret is
                                # mounted as single file named config.json.
                                {"name": "DOCKER_CONFIG", "value": "/home/user/.docker"},
                            ],
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": "/workspace", "readOnly": True},
                                {"name": "harbor-docker-cfg", "mountPath": "/home/user/.docker", "readOnly": True},
                                # BuildKit snapshotter + metadata DB live here;
                                # emptyDir backed by node disk (not tmpfs).
                                {"name": "buildkit-state", "mountPath": "/home/user/.local/share/buildkit"},
                                # buildctl writes temporary frontend files here.
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                            "securityContext": buildkit_sc,
                            "resources": {
                                "requests": {
                                    "cpu": "1",
                                    "memory": "2Gi",
                                    # BuildKit stores layers to disk via
                                    # overlay snapshotter; the 30Gi
                                    # buildkit-state emptyDir covers DL-image
                                    # snapshots. A small floor here keeps the
                                    # scheduler aware of build-disk pressure.
                                    "ephemeral-storage": "2Gi",
                                },
                                # Community-observed RSS for multi-GB CUDA
                                # bases with rootless BuildKit is 2-4 GiB —
                                # materially below Kaniko's 20Gi requirement
                                # because BuildKit does NOT load the whole
                                # post-RUN filesystem into tmpfs per layer.
                                # 8Gi gives ample headroom and lets the node
                                # run two concurrent builds (vs one for
                                # Kaniko on an 8-GPU 32Gi server).
                                "limits": {
                                    "cpu": "2",
                                    "memory": "8Gi",
                                    "ephemeral-storage": "32Gi",
                                },
                            },
                        }
                    ],
                },
            },
        },
    }
