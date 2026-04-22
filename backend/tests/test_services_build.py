from uuid import uuid4

from app.config import settings
from app.services.build import (
    JOB_TTL_SECONDS,
    _slugify,
    build_git_credential_secret,
    build_job_name,
    build_job_spec,
)


def test_job_spec_has_three_containers_and_security():
    build_id = uuid4()
    job = build_job_spec(
        build_id=build_id,
        detector_name="upxelfdet",
        git_tag="v0.1.0",
        owner_repo="bolin8017/upxelfdet",
    )
    spec = job["spec"]["template"]["spec"]

    assert len(spec["initContainers"]) == 2
    assert {c["name"] for c in spec["initContainers"]} == {"clone", "validate"}
    assert len(spec["containers"]) == 1
    assert spec["containers"][0]["name"] == "buildkit"

    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    # initContainers (clone, validate) stay minimally privileged — dropped
    # ALL caps, no privilege escalation. Only the buildkit container
    # relaxes these (rootlesskit needs setuid newuidmap; see
    # test_buildkit_container_is_rootless_not_privileged below for the
    # specific assertions that substitute for these on that container).
    for c in spec["initContainers"]:
        sc = c["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"]["drop"] == ["ALL"]

    assert job["spec"]["activeDeadlineSeconds"] == 1200
    # Contract: spec reflects the module constant — don't hardcode the
    # value so tuning JOB_TTL_SECONDS doesn't require a test update.
    assert job["spec"]["ttlSecondsAfterFinished"] == JOB_TTL_SECONDS
    assert job["spec"]["backoffLimit"] == 0


def test_buildkit_container_is_rootless_not_privileged():
    """Regression guard: BuildKit's security context must match the
    upstream moby/buildkit rootless example — runAsUser=1000 plus
    Unconfined seccomp/AppArmor so rootlesskit can set up the user
    namespace.

    DO NOT set allowPrivilegeEscalation=false or drop capabilities
    here: rootlesskit invokes setuid newuidmap/newgidmap to populate
    /proc/<pid>/uid_map, and `no_new_privs` + dropped CAP_SETUID break
    those binaries with 'operation not permitted'. We already have the
    meaningful security properties — non-root pod UID, user namespace
    isolation, no privileged: true — and adding allowPrivilegeEscalation=false
    only gets us a non-functional build pipeline.
    """
    job = build_job_spec(
        build_id=uuid4(),
        detector_name="upxelfdet",
        git_tag="v0.1.0",
        owner_repo="o/x",
    )
    spec = job["spec"]["template"]["spec"]
    buildkit = spec["containers"][0]
    assert buildkit["name"] == "buildkit"
    sc = buildkit["securityContext"]
    assert sc["runAsUser"] == 1000
    assert sc["runAsGroup"] == 1000
    assert sc["runAsNonRoot"] is True
    # Strict: `is not True` would be satisfied by a stray `privileged: 1`
    # or `privileged: "true"` slipping in from a YAML refactor. Demand
    # `False` (or absent) explicitly.
    assert sc.get("privileged", False) is False
    # BuildKit-specific Unconfined profiles (pod-level default is
    # RuntimeDefault; container override is intentional).
    assert sc["seccompProfile"]["type"] == "Unconfined"
    assert sc["appArmorProfile"]["type"] == "Unconfined"
    # No `procMount: Unmasked` cargo-culted from a forum post. The
    # default (None / masked) is what rootless BuildKit expects.
    assert sc.get("procMount") in (None, "Default")
    # No escape-hatch container-level hostPath volumes / raw block devices.
    assert not buildkit.get("volumeDevices")
    # Pod-level escape hatches must stay off too — a helpful contributor
    # enabling hostNetwork/hostPID/hostIPC "for debugging" breaks the
    # user-namespace isolation that is the whole point.
    assert spec.get("hostNetwork") is not True
    assert spec.get("hostPID") is not True
    assert spec.get("hostIPC") is not True
    # No pod volume should use hostPath (the build pipeline is designed
    # to read everything through emptyDir + Secret mounts).
    for vol in spec["volumes"]:
        assert "hostPath" not in vol, f"volume {vol.get('name')} uses hostPath — escape hatch"


def test_buildkit_destination_and_insecure_registry_flags():
    """buildctl-daemonless args must carry the Harbor destination and
    mark the registry insecure (Harbor in-cluster is plain HTTP on :80).
    """
    job = build_job_spec(
        build_id=uuid4(),
        detector_name="upxelfdet",
        git_tag="v0.1.0",
        owner_repo="bolin8017/upxelfdet",
    )
    buildkit = job["spec"]["template"]["spec"]["containers"][0]
    # Exec form — command is the wrapper, args is argv.
    assert buildkit["command"] == ["buildctl-daemonless.sh"]
    args = buildkit["args"]
    assert args[0] == "build"
    # The image-target triple MUST travel as one comma-separated
    # --output token, not three separate outputs; BuildKit would happily
    # interpret `--output push=true` as a second output target and drop
    # the image push silently.
    output_idx = args.index("--output")
    output_val = args[output_idx + 1]
    assert output_val.startswith("type=image,")
    # Accept any Harbor prefix (default vs helm-overridden), just pin
    # the project/name/tag tail that identifies the target image.
    assert "/detectors/upxelfdet:v0.1.0" in output_val
    assert "push=true" in output_val
    assert "registry.insecure=true" in output_val
    # Registry-backed cache, exported and imported with the same ref.
    export_idx = args.index("--export-cache")
    import_idx = args.index("--import-cache")
    assert args[export_idx + 1].startswith("type=registry,")
    assert args[import_idx + 1].startswith("type=registry,")
    assert "/detectors-cache/upxelfdet" in args[export_idx + 1]
    assert "/detectors-cache/upxelfdet" in args[import_idx + 1]


def test_buildkit_image_from_settings_not_hardcoded():
    """BUILD_IMAGE_BUILDKIT must flow from the Settings field, so
    values.yaml can pin a different tag without a backend rebuild.
    Regression guard against someone hard-coding the tag in build.py.
    """
    job = build_job_spec(
        build_id=uuid4(),
        detector_name="x",
        git_tag="v0.1.0",
        owner_repo="o/x",
    )
    buildkit = job["spec"]["template"]["spec"]["containers"][0]
    assert buildkit["image"] == settings.BUILD_IMAGE_BUILDKIT


def test_buildkit_container_has_required_env():
    """BUILDKITD_FLAGS and DOCKER_CONFIG are both load-bearing:
      - BUILDKITD_FLAGS=--oci-worker-no-process-sandbox is the K8s
        equivalent of Docker's systempaths=unconfined; without it the
        rootless daemon fails to start inside the pod.
      - DOCKER_CONFIG points buildctl at the mounted Harbor dockerconfigjson;
        otherwise pushes get 401 on unauthenticated.
    """
    job = build_job_spec(
        build_id=uuid4(),
        detector_name="x",
        git_tag="v0.1.0",
        owner_repo="o/x",
    )
    buildkit = job["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in buildkit["env"]}
    assert env.get("BUILDKITD_FLAGS") == "--oci-worker-no-process-sandbox"
    assert env.get("DOCKER_CONFIG") == "/home/user/.docker"
    # Explicit retry envelope so the daemonless wrapper surfaces a real
    # error instead of hanging when buildkitd doesn't start cleanly.
    assert env.get("BUILDCTL_CONNECT_RETRIES_ON_STARTUP") == "10"


def test_buildkit_state_volume_writable():
    """BuildKit's overlay snapshotter writes to $HOME/.local/share/buildkit.
    Must be an emptyDir (not a secret / configMap / readOnly mount).
    """
    job = build_job_spec(
        build_id=uuid4(),
        detector_name="x",
        git_tag="v0.1.0",
        owner_repo="o/x",
    )
    spec = job["spec"]["template"]["spec"]
    state_vol = next(v for v in spec["volumes"] if v["name"] == "buildkit-state")
    assert "emptyDir" in state_vol
    buildkit = spec["containers"][0]
    state_mount = next(m for m in buildkit["volumeMounts"] if m["name"] == "buildkit-state")
    assert state_mount["mountPath"] == "/home/user/.local/share/buildkit"
    assert state_mount.get("readOnly") is not True


def test_git_credential_secret_contains_token_and_build_token():
    secret = build_git_credential_secret(
        build_id=uuid4(),
        username="bolin8017",
        pat_token="ghp_xxx",
        build_token="btok_abc",
    )
    assert secret["type"] == "Opaque"
    data = secret["stringData"]
    assert data["username"] == "bolin8017"
    assert data["token"] == "ghp_xxx"
    assert data["build_token"] == "btok_abc"


def test_slugify_lowercases():
    assert _slugify("Hello World") == "hello-world"


def test_slugify_strips_trailing_hyphens():
    assert _slugify("abc...") == "abc"


def test_slugify_truncates_to_63_chars():
    s = _slugify("a" * 100)
    assert len(s) == 63
    assert s == "a" * 63


def test_slugify_collapses_consecutive_hyphens():
    assert _slugify("foo---bar") == "foo-bar"


def test_build_job_name_k8s_safe():
    from uuid import UUID
    name = build_job_name("UPXelfdet", "v0.1.0", UUID("12345678-1234-5678-1234-567812345678"))
    # must be lowercase DNS-1123, <= 63 chars, no dots
    assert name.islower()
    assert len(name) <= 63
    assert "." not in name
    assert name.startswith("build-upxelfdet-v0-1-0-")


def test_build_containers_have_ephemeral_storage_limits():
    """Without these, a runaway build (e.g. a DL-image layer) triggers
    node-level eviction instead of just getting its own pod evicted.
    Phase 8 run saw 5 failed DL build pods collectively fill 28Gi of node
    ephemeral storage because none of the containers had the limit set.
    """
    job = build_job_spec(
        build_id=uuid4(),
        detector_name="x",
        git_tag="v0.1.0",
        owner_repo="o/x",
    )
    spec = job["spec"]["template"]["spec"]
    for c in spec["initContainers"] + spec["containers"]:
        assert "ephemeral-storage" in c["resources"]["requests"], (
            f"{c['name']} missing ephemeral-storage request"
        )
        assert "ephemeral-storage" in c["resources"]["limits"], (
            f"{c['name']} missing ephemeral-storage limit"
        )
