"""Phase 4 D4.2 R6 — unit tests for scripts/lib/harbor_api.py.

Runs from the backend uv environment (`cd backend && uv run pytest ../scripts/tests/lib/`).
Uses respx to mock the Harbor REST endpoints and monkeypatched
subprocess for the kubectl probe path.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
import respx

from scripts.lib import harbor_api

# ---------- _is_safe_sha / _is_sha256_digest --------------------------


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("0123456789ab", True),
        ("f" * 64, True),
        ("abc", False),
        ("g" * 12, False),
        ("", False),
        ("0123456789ab; rm -rf /", False),
    ],
)
def test_is_safe_sha(value: str, ok: bool) -> None:
    assert harbor_api._is_safe_sha(value) is ok


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("sha256:" + "a" * 64, True),
        ("sha256:" + "a" * 63, False),
        ("sha512:" + "a" * 64, False),
        ("a" * 64, False),
    ],
)
def test_is_sha256_digest(value: str, ok: bool) -> None:
    assert harbor_api._is_sha256_digest(value) is ok


# ---------- decode_dockerconfig / build_dockerconfig ------------------


def test_build_dockerconfig_registers_both_hosts() -> None:
    encoded = harbor_api.build_dockerconfig(
        user="robot$build-pusher",
        secret="s3cret",
        host_alias="harbor.lolday.svc.cluster.local:80",
    )
    cfg = json.loads(base64.b64decode(encoded).decode())
    assert "harbor.lolday.svc:80" in cfg["auths"]
    assert "harbor.lolday.svc.cluster.local:80" in cfg["auths"]
    enc = cfg["auths"]["harbor.lolday.svc:80"]["auth"]
    assert base64.b64decode(enc).decode() == "robot$build-pusher:s3cret"


def test_decode_dockerconfig_picks_svc_alias_by_default() -> None:
    encoded = harbor_api.build_dockerconfig(
        "u", "p", "harbor.lolday.svc.cluster.local:80"
    )
    cfg = base64.b64decode(encoded).decode()
    assert harbor_api.decode_dockerconfig(cfg) == "u:p"


def test_decode_dockerconfig_falls_back_to_single_entry() -> None:
    cfg = json.dumps(
        {"auths": {"some-other-host:443": {"auth": base64.b64encode(b"u:p").decode()}}}
    )
    assert harbor_api.decode_dockerconfig(cfg) == "u:p"


def test_decode_dockerconfig_raises_on_ambiguous_missing_default() -> None:
    cfg = json.dumps(
        {
            "auths": {
                "a.example:80": {"auth": "x"},
                "b.example:80": {"auth": "y"},
            }
        }
    )
    with pytest.raises(KeyError, match="cannot disambiguate"):
        harbor_api.decode_dockerconfig(cfg)


# ---------- parse_robot_list / robot_state / add_cache_perm -----------


def test_parse_robot_list_picks_matching_name() -> None:
    body = json.dumps(
        [
            {"id": 1, "name": "robot$other"},
            {"id": 42, "name": "robot$build-pusher"},
        ]
    )
    assert harbor_api.parse_robot_list(body) == "42"


def test_parse_robot_list_handles_legacy_unprefixed_name() -> None:
    body = json.dumps([{"id": 7, "name": "build-pusher"}])
    assert harbor_api.parse_robot_list(body) == "7"


def test_parse_robot_list_returns_empty_on_no_match() -> None:
    body = json.dumps([{"id": 1, "name": "robot$something"}])
    assert harbor_api.parse_robot_list(body) == ""


def test_parse_robot_list_returns_empty_on_bad_json() -> None:
    assert harbor_api.parse_robot_list("not-json") == ""


@pytest.mark.parametrize(
    ("perms", "expected"),
    [
        ([], "empty"),
        ([{"namespace": "lolday"}], "missing-core"),
        ([{"namespace": "lolday"}, {"namespace": "detectors"}], "needs-cache"),
        (
            [
                {"namespace": "lolday"},
                {"namespace": "detectors"},
                {"namespace": "detectors-cache"},
            ],
            "already-has-cache",
        ),
    ],
)
def test_robot_state(perms: list[dict[str, Any]], expected: str) -> None:
    body = json.dumps({"permissions": perms})
    assert harbor_api.robot_state(body) == expected


def test_add_cache_perm_appends_detectors_cache() -> None:
    body = json.dumps(
        {
            "name": "build-pusher",
            "level": "system",
            "duration": 90,
            "permissions": [
                {"kind": "project", "namespace": "lolday", "access": []},
                {"kind": "project", "namespace": "detectors", "access": []},
            ],
        }
    )
    out = json.loads(harbor_api.add_cache_perm(body))
    namespaces = {p["namespace"] for p in out["permissions"]}
    assert namespaces == {"lolday", "detectors", "detectors-cache"}
    assert out["name"] == "build-pusher"
    assert out["level"] == "system"


def test_redact_robot_response_hides_secret() -> None:
    body = json.dumps({"id": 5, "name": "build-pusher", "secret": "super-secret"})
    redacted = json.loads(harbor_api.redact_robot_response(body))
    assert redacted["secret"] == "<redacted>"
    assert redacted["id"] == 5
    assert redacted["name"] == "build-pusher"


# ---------- has_tag / get_digest via respx ----------------------------


@pytest.fixture
def stub_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend `kubectl get secret` returns a valid dockerconfigjson
    so the auth-header builder works without a real cluster."""
    cfg = harbor_api.build_dockerconfig(
        "robot$build-pusher",
        "secret",
        "harbor.lolday.svc.cluster.local:80",
    )
    decoded = base64.b64decode(cfg).decode()

    def fake_get_secret(namespace: str, name: str) -> str | None:
        if namespace == "lolday" and name == "harbor-push-cred":
            return decoded
        return None

    monkeypatch.setattr(harbor_api, "_kubectl_get_secret", fake_get_secret)
    monkeypatch.delenv("HARBOR_CRED_NS", raising=False)


@respx.mock
def test_has_tag_returns_true_on_non_empty_artifact_list(stub_creds: None) -> None:
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(200, json=[{"id": 1}])
    assert harbor_api.has_tag("build-helper", "0123456789ab") is True


@respx.mock
def test_has_tag_returns_false_on_empty_list(stub_creds: None) -> None:
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(200, json=[])
    assert harbor_api.has_tag("build-helper", "0123456789ab") is False


@respx.mock
def test_has_tag_returns_false_on_404(stub_creds: None) -> None:
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(404)
    assert harbor_api.has_tag("build-helper", "0123456789ab") is False


def test_has_tag_refuses_unsafe_sha() -> None:
    with pytest.raises(ValueError, match="non-SHA"):
        harbor_api.has_tag("build-helper", "; rm -rf /")


@respx.mock
def test_get_digest_returns_pinned_digest(stub_creds: None) -> None:
    digest = "sha256:" + "a" * 64
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(200, json=[{"digest": digest}])
    assert harbor_api.get_digest("build-helper", "0123456789ab") == digest


@respx.mock
def test_get_digest_raises_on_empty_list(stub_creds: None) -> None:
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(200, json=[])
    with pytest.raises(RuntimeError, match="empty artifact list"):
        harbor_api.get_digest("build-helper", "0123456789ab")


@respx.mock
def test_get_digest_raises_on_malformed_digest(stub_creds: None) -> None:
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(200, json=[{"digest": "sha512:" + "a" * 128}])
    with pytest.raises(RuntimeError, match="unexpected digest"):
        harbor_api.get_digest("build-helper", "0123456789ab")


# ---------- creds_namespace -------------------------------------------


def test_creds_namespace_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARBOR_CRED_NS", "custom-ns")
    assert harbor_api.creds_namespace() == "custom-ns"


def test_creds_namespace_probes_lolday_then_lolday_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HARBOR_CRED_NS", raising=False)
    calls: list[tuple[str, str]] = []

    def fake_get_secret(namespace: str, name: str) -> str | None:
        calls.append((namespace, name))
        return "{}" if namespace == "lolday-jobs" else None

    monkeypatch.setattr(harbor_api, "_kubectl_get_secret", fake_get_secret)
    assert harbor_api.creds_namespace() == "lolday-jobs"
    assert calls == [
        ("lolday", "harbor-push-cred"),
        ("lolday-jobs", "harbor-push-cred"),
    ]


def test_creds_namespace_raises_when_secret_missing_everywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HARBOR_CRED_NS", raising=False)
    monkeypatch.setattr(harbor_api, "_kubectl_get_secret", lambda ns, n: None)
    with pytest.raises(RuntimeError, match="not found in any of"):
        harbor_api.creds_namespace()
