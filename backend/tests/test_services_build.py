from uuid import uuid4

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
    buildkit = job["spec"]["template"]["spec"]["containers"][0]
    assert buildkit["name"] == "buildkit"
    sc = buildkit["securityContext"]
    assert sc["runAsUser"] == 1000
    assert sc["runAsGroup"] == 1000
    assert sc["runAsNonRoot"] is True
    # The important negative assertion: never flip to privileged.
    assert sc.get("privileged") is not True
    # BuildKit-specific Unconfined profiles (pod-level default is
    # RuntimeDefault; container override is intentional).
    assert sc["seccompProfile"]["type"] == "Unconfined"
    assert sc["appArmorProfile"]["type"] == "Unconfined"


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
    # Entire buildctl invocation is in the shell-wrapped args[0].
    args_text = " ".join(buildkit["args"])
    assert "buildctl-daemonless.sh build" in args_text
    assert "--frontend dockerfile.v0" in args_text
    assert "--local context=/workspace/src" in args_text
    assert "--local dockerfile=/workspace/src" in args_text
    # Image target + push + insecure flag must travel together.
    assert "name=harbor" in args_text
    assert "/detectors/upxelfdet:v0.1.0" in args_text
    assert "push=true" in args_text
    assert "registry.insecure=true" in args_text
    # Registry-backed cache for layer reuse across builds.
    assert "--export-cache type=registry" in args_text
    assert "--import-cache type=registry" in args_text
    assert "/detectors-cache/upxelfdet" in args_text


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
