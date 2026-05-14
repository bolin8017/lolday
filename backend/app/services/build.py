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
    build_id: UUID, username: str, pat_token: str
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": build_secret_name(build_id)},
        "type": "Opaque",
        "stringData": {
            "username": username,
            "token": pat_token,
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
    #
    # HOST PREREQUISITE (Ubuntu 24.04+): the node must have
    #   kernel.apparmor_restrict_unprivileged_userns = 0
    # in /etc/sysctl.d/ — without it rootlesskit fails with
    #   `[rootlesskit:parent] error: ... EPERM`
    # See docs/runbooks/deploy.md §1 (Pre-requisites) for the full host setup.
    # H-11 (P2): use a custom Localhost-type seccomp profile (the Docker
    # Engine default whitelist that BuildKit-rootless docs reference)
    # instead of Unconfined. The profile must exist at
    #   /var/lib/kubelet/seccomp/profiles/buildkit-rootless.json
    # on every node — installed by the buildkit-seccomp-installer DaemonSet
    # (charts/lolday/templates/buildkit-seccomp-installer.yaml), which is
    # gated on Values.buildkit.seccompProfile.enabled. The profile
    # whitelists the user-namespace syscalls (setuid/setgid/clone/unshare)
    # that rootlesskit needs, which the Restricted-default RuntimeDefault
    # blocks.
    #
    # appArmorProfile stays Unconfined — that's an orthogonal control
    # with different infrastructure requirements (per-node AppArmor
    # profile loading), tracked separately.
    buildkit_sc = {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "seccompProfile": {
            "type": "Localhost",
            "localhostProfile": "profiles/buildkit-rootless.json",
        },
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
                        # buildkit's daemonless wrapper materialises transient
                        # frontend artefacts (parsed Dockerfile, intermediate
                        # build state, the squashed-layer overflow buffer when
                        # exporting to registry) under /tmp. With CUDA-base
                        # images the squashed final layer alone can be 5-8 GiB
                        # before push, so 12 GiB gives buildkit enough headroom
                        # without bleeding into node ephemeral-storage pressure.
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
                        # Phase 11c: shared volume between the validate
                        # initContainer (writes per-key MALDET_* files) and
                        # the buildkit container (reads them and emits
                        # --opt build-arg:KEY=VAL flags). Tiny payload
                        # (5 strings, manifest_b64 the largest at ~1KB).
                        {
                            "name": "build-args",
                            "emptyDir": {"sizeLimit": "1Mi"},
                        },
                    ],
                    "initContainers": [
                        {
                            "name": "clone",
                            "image": settings.BUILD_IMAGE_GIT,
                            "command": ["/bin/sh", "-c"],
                            "args": [
                                "set +x; "
                                # H-19: git PAT must NOT appear in argv. Use git's
                                # credential helper — the inline helper script
                                # reads $GIT_USER and $GIT_TOKEN from env (which
                                # are valueFrom: secretKeyRef, not visible in
                                # kubectl describe pod) and echoes them on
                                # stdout for git to consume. The clone URL no
                                # longer carries any user:pass component.
                                # L-clone-bandwidth: --filter=blob:limit=10m refuses blobs > 10 MiB at
                                # transfer time -- caps disk + bandwidth from a malicious repo before
                                # the validator (which itself enforces REPO_MAX_SIZE_MB post-clone) runs.
                                # See plan section D7 for why validator.py needs no separate edit.
                                "git -c credential.helper='!f() { echo username=$GIT_USER; echo password=$GIT_TOKEN; }; f' "
                                "clone --depth=1 --filter=blob:limit=10m --recurse-submodules "
                                '--branch="$GIT_TAG" '
                                '"https://github.com/$REPO.git" '
                                "/workspace/src && "
                                "git -C /workspace/src rev-parse HEAD > /workspace/git-sha"
                            ],
                            "env": [
                                {"name": "GIT_TAG", "value": git_tag},
                                {"name": "REPO", "value": owner_repo},
                                {
                                    "name": "GIT_USER",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": secret_name,
                                            "key": "username",
                                        }
                                    },
                                },
                                {
                                    "name": "GIT_TOKEN",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": secret_name,
                                            "key": "token",
                                        }
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
                            # Phase 11c: validator now takes (repo_path,
                            # build_args_out). It validates the repo and
                            # writes the 5 MALDET_* per-key files to the
                            # build-args dir for the buildkit container to
                            # consume.
                            "args": ["/workspace/src", "/workspace/build-args"],
                            "env": [
                                {"name": "BUILD_ID", "value": str(build_id)},
                            ],
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": "/workspace"},
                                {"name": "tmp", "mountPath": "/tmp"},
                                # Writable: validator emits the per-key
                                # MALDET_* files here.
                                {
                                    "name": "build-args",
                                    "mountPath": "/workspace/build-args",
                                },
                            ],
                            "securityContext": base_sc,
                            # Phase 11c validator only parses ``maldet.toml``
                            # (tomllib + pydantic ``DetectorManifest``) and
                            # writes 5 small files (~few hundred KB total,
                            # MALDET_MANIFEST_B64 dominates) to the build-args
                            # dir. No pip install, no source import. Peak
                            # memory hovers around 60-80 MiB; the 256 MiB
                            # limit is a generous ceiling so a runaway pydantic
                            # error chain doesn't escape silently.
                            "resources": {
                                "requests": {
                                    "cpu": "100m",
                                    "memory": "128Mi",
                                    "ephemeral-storage": "128Mi",
                                },
                                "limits": {
                                    "cpu": "500m",
                                    "memory": "256Mi",
                                    "ephemeral-storage": "1Gi",
                                },
                            },
                        },
                    ],
                    "containers": [
                        {
                            "name": "buildkit",
                            "image": settings.BUILD_IMAGE_BUILDKIT,
                            "imagePullPolicy": "IfNotPresent",
                            # Phase 11c: wrap buildctl-daemonless.sh in a
                            # `sh -c` shell so we can read the 5 per-key
                            # MALDET_* files written by the validate
                            # initContainer and forward them as
                            # `--opt build-arg:KEY=VAL` flags. The
                            # f-string-interpolated values (destination,
                            # cache_repo) are baked at Python-render time
                            # and cannot be subverted by the build-args
                            # contents (which are read at runtime as $MN,
                            # $MV, …). git_tag is still validated by the
                            # schema-level regex upstream of build_job_spec.
                            "command": ["/bin/sh", "-c"],
                            "args": [
                                "set -eu; "
                                "BA=/workspace/build-args; "
                                "MN=$(cat $BA/MALDET_NAME); "
                                "MV=$(cat $BA/MALDET_VERSION); "
                                "MF=$(cat $BA/MALDET_FRAMEWORK); "
                                "MB=$(cat $BA/MALDET_MANIFEST_B64); "
                                "GC=$(cat $BA/GIT_COMMIT); "
                                "exec buildctl-daemonless.sh build "
                                "--frontend dockerfile.v0 "
                                "--local context=/workspace/src "
                                "--local dockerfile=/workspace/src "
                                f"--output type=image,name={destination},push=true,registry.insecure=true "
                                f"--export-cache type=registry,ref={cache_repo},mode=max,registry.insecure=true "
                                f"--import-cache type=registry,ref={cache_repo},registry.insecure=true "
                                "--progress plain "
                                '--opt build-arg:MALDET_NAME="$MN" '
                                '--opt build-arg:MALDET_VERSION="$MV" '
                                '--opt build-arg:MALDET_FRAMEWORK="$MF" '
                                '--opt build-arg:MALDET_MANIFEST_B64="$MB" '
                                '--opt build-arg:GIT_COMMIT="$GC" '
                                # Stamp the commit SHA into the standard OCI
                                # revision label so the reconciler can read it
                                # back without depending on the detector
                                # author's Dockerfile to set it. Read by
                                # _handle_succeeded → DetectorVersion.git_sha.
                                '--opt label:org.opencontainers.image.revision="$GC"'
                            ],
                            "env": [
                                # buildctl-daemonless.sh retries daemon
                                # startup this many times before exiting 1.
                                {
                                    "name": "BUILDCTL_CONNECT_RETRIES_ON_STARTUP",
                                    "value": "10",
                                },
                                # Required for rootless in Kubernetes. Docker
                                # sets `systempaths=unconfined` via daemon.json;
                                # we don't have that, so explicitly disable the
                                # process sandbox.
                                {
                                    "name": "BUILDKITD_FLAGS",
                                    "value": "--oci-worker-no-process-sandbox",
                                },
                                # buildctl reads $DOCKER_CONFIG/config.json to
                                # authenticate to the registry; the secret is
                                # mounted as single file named config.json.
                                {
                                    "name": "DOCKER_CONFIG",
                                    "value": "/home/user/.docker",
                                },
                            ],
                            "volumeMounts": [
                                {
                                    "name": "workspace",
                                    "mountPath": "/workspace",
                                    "readOnly": True,
                                },
                                {
                                    "name": "harbor-docker-cfg",
                                    "mountPath": "/home/user/.docker",
                                    "readOnly": True,
                                },
                                # BuildKit snapshotter + metadata DB live here;
                                # emptyDir backed by node disk (not tmpfs).
                                {
                                    "name": "buildkit-state",
                                    "mountPath": "/home/user/.local/share/buildkit",
                                },
                                # buildctl writes temporary frontend files here.
                                {"name": "tmp", "mountPath": "/tmp"},
                                # Phase 11c: per-key MALDET_* files written
                                # by the validate initContainer; read by
                                # the sh -c wrapper above. Read-only — we
                                # never want buildkitd writing into this
                                # directory.
                                {
                                    "name": "build-args",
                                    "mountPath": "/workspace/build-args",
                                    "readOnly": True,
                                },
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
