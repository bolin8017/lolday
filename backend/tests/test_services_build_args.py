"""Tests that build_job_spec produces a Job that reads & forwards build-args."""

from __future__ import annotations

from uuid import uuid4

from app.services.build import build_job_spec


def _spec() -> dict:
    return build_job_spec(
        build_id=uuid4(),
        detector_name="elfrfdet",
        git_tag="v2.0.0",
        owner_repo="bolin8017/elfrfdet",
    )


def test_build_args_emptydir_volume_present() -> None:
    spec = _spec()
    vols = {v["name"] for v in spec["spec"]["template"]["spec"]["volumes"]}
    assert "build-args" in vols


def test_validate_container_mounts_build_args_writable() -> None:
    spec = _spec()
    init = next(
        c
        for c in spec["spec"]["template"]["spec"]["initContainers"]
        if c["name"] == "validate"
    )
    mount = next(m for m in init["volumeMounts"] if m["name"] == "build-args")
    # Phase 11c contract: validate writes the per-key files here.
    assert mount["mountPath"] == "/workspace/build-args"
    assert not mount.get("readOnly", False)


def test_validate_container_passes_build_args_dir_in_argv() -> None:
    spec = _spec()
    init = next(
        c
        for c in spec["spec"]["template"]["spec"]["initContainers"]
        if c["name"] == "validate"
    )
    # Validator now takes (repo_path, build_args_out).
    assert init["args"] == ["/workspace/src", "/workspace/build-args"]


def test_buildkit_container_mounts_build_args_readonly() -> None:
    spec = _spec()
    bk = next(
        c
        for c in spec["spec"]["template"]["spec"]["containers"]
        if c["name"] == "buildkit"
    )
    mount = next(m for m in bk["volumeMounts"] if m["name"] == "build-args")
    assert mount["mountPath"] == "/workspace/build-args"
    assert mount["readOnly"] is True


def test_buildkit_command_assembles_build_args_from_files() -> None:
    """The buildkit container reads the per-file args and emits --opt build-arg:KEY=VAL.

    A regression here would silently produce an image with empty manifest
    labels (the very bug Phase 11c fixes).
    """
    spec = _spec()
    bk = next(
        c
        for c in spec["spec"]["template"]["spec"]["containers"]
        if c["name"] == "buildkit"
    )
    cmd_argv = bk["command"] + bk["args"]
    joined = " ".join(cmd_argv)
    for key in (
        "MALDET_NAME",
        "MALDET_VERSION",
        "MALDET_FRAMEWORK",
        "MALDET_MANIFEST_B64",
        "GIT_COMMIT",
    ):
        assert key in joined, f"buildkit args do not reference {key}"
    # Exactly five --opt build-arg flags — drop one and the missing build
    # arg makes the buildkit Dockerfile sub a default, silently producing
    # an image whose manifest label is empty / stale.
    assert joined.count("--opt build-arg:") == 5
