from uuid import uuid4

from app.services.build import (
    build_git_credential_secret,
    build_job_spec,
)
from app.services.build import _slugify, build_job_name


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
    assert spec["containers"][0]["name"] == "kaniko"

    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    for c in spec["initContainers"] + spec["containers"]:
        sc = c["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"]["drop"] == ["ALL"]

    assert job["spec"]["activeDeadlineSeconds"] == 1200
    assert job["spec"]["ttlSecondsAfterFinished"] == 604800
    assert job["spec"]["backoffLimit"] == 0


def test_job_spec_kaniko_destination_matches_harbor_prefix():
    job = build_job_spec(
        build_id=uuid4(),
        detector_name="upxelfdet",
        git_tag="v0.1.0",
        owner_repo="bolin8017/upxelfdet",
    )
    kaniko = job["spec"]["template"]["spec"]["containers"][0]
    dest_arg = next(a for a in kaniko["args"] if a.startswith("--destination="))
    assert dest_arg.endswith("/detectors/upxelfdet:v0.1.0")


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
