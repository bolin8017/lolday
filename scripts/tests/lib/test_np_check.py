"""Phase 4 D4.5 R6 — unit tests for scripts/lib/np_check.py."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scripts.lib import np_check


def _write_yaml(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def _make_np(
    name: str,
    selector: dict[str, str],
    port: int,
    *,
    from_ns: str = "kube-system",
    from_pod: str = "traefik",
    protocol: str = "TCP",
) -> str:
    """Render a single NetworkPolicy doc matching the structure helm produces.
    Defaults satisfy the check; override fields to exercise failure paths."""
    return (
        "---\n"
        "apiVersion: networking.k8s.io/v1\n"
        "kind: NetworkPolicy\n"
        f"metadata:\n  name: {name}\n  namespace: lolday\n"
        "spec:\n"
        "  podSelector:\n"
        f"    matchLabels: {selector!r}\n"
        "  policyTypes: [Ingress]\n"
        "  ingress:\n"
        "    - from:\n"
        "        - namespaceSelector:\n"
        f"            matchLabels: {{kubernetes.io/metadata.name: {from_ns}}}\n"
        "          podSelector:\n"
        f"            matchLabels: {{app.kubernetes.io/name: {from_pod}}}\n"
        "      ports:\n"
        f"        - protocol: {protocol}\n"
        f"          port: {port}\n"
    )


def _three_targets_yaml() -> str:
    """A rendered chart that satisfies all three DEFAULT_TARGETS rows."""
    return "\n".join(
        [
            _make_np(name, dict(selector), port)
            for name, selector, port in np_check.DEFAULT_TARGETS
        ]
    )


def test_clean_when_all_three_targets_present(tmp_path: Path) -> None:
    f = tmp_path / "rendered.yaml"
    _write_yaml(f, _three_targets_yaml())
    assert np_check.check_user_facing_np(str(f)) == []


def test_missing_np_is_flagged(tmp_path: Path) -> None:
    f = tmp_path / "rendered.yaml"
    # render only backend + frontend; mlflow-ingress-allow is absent.
    yaml_body = "\n".join(
        _make_np(name, dict(sel), port)
        for name, sel, port in np_check.DEFAULT_TARGETS[:2]
    )
    _write_yaml(f, yaml_body)
    fails = np_check.check_user_facing_np(str(f))
    assert len(fails) == 1
    assert "mlflow-ingress-allow" in fails[0]
    assert "not rendered" in fails[0]


def test_wrong_pod_selector_is_flagged(tmp_path: Path) -> None:
    f = tmp_path / "rendered.yaml"
    yaml_body = _make_np(
        "backend-metrics-from-monitoring-only",
        {"app.kubernetes.io/component": "wrong-component"},
        8000,
    )
    yaml_body += "\n" + "\n".join(
        _make_np(name, dict(sel), port)
        for name, sel, port in np_check.DEFAULT_TARGETS[1:]
    )
    _write_yaml(f, yaml_body)
    fails = np_check.check_user_facing_np(str(f))
    assert len(fails) == 1
    assert "backend-metrics-from-monitoring-only" in fails[0]
    assert "podSelector" in fails[0]


def test_wrong_pod_side_port_is_flagged(tmp_path: Path) -> None:
    """Common P1 H-25 regression class: NP keyed on Service port (8443)
    instead of POD targetPort (8000). kube-router enforces post-DNAT, so
    the rule silently denies."""
    f = tmp_path / "rendered.yaml"
    yaml_body = _make_np(
        "backend-metrics-from-monitoring-only",
        {"app.kubernetes.io/component": "backend"},
        8443,  # wrong: Service port instead of POD targetPort
    )
    yaml_body += "\n" + "\n".join(
        _make_np(name, dict(sel), port)
        for name, sel, port in np_check.DEFAULT_TARGETS[1:]
    )
    _write_yaml(f, yaml_body)
    fails = np_check.check_user_facing_np(str(f))
    assert len(fails) == 1
    assert "backend-metrics-from-monitoring-only" in fails[0]
    assert "POD-side port 8000/TCP" in fails[0]


def test_non_traefik_source_is_flagged(tmp_path: Path) -> None:
    """Regression P1 H-25: ingress allowed cloudflared@lolday instead of
    Traefik. cloudflared dials Traefik, not the backend pod, so this
    rule is wrong even though it parses."""
    f = tmp_path / "rendered.yaml"
    yaml_body = _make_np(
        "backend-metrics-from-monitoring-only",
        {"app.kubernetes.io/component": "backend"},
        8000,
        from_ns="lolday",
        from_pod="cloudflared",
    )
    yaml_body += "\n" + "\n".join(
        _make_np(name, dict(sel), port)
        for name, sel, port in np_check.DEFAULT_TARGETS[1:]
    )
    _write_yaml(f, yaml_body)
    fails = np_check.check_user_facing_np(str(f))
    assert len(fails) == 1
    assert "kube-system + app.kubernetes.io/name=traefik" in fails[0]


def test_udp_protocol_does_not_satisfy_tcp_check(tmp_path: Path) -> None:
    f = tmp_path / "rendered.yaml"
    yaml_body = _make_np(
        "backend-metrics-from-monitoring-only",
        {"app.kubernetes.io/component": "backend"},
        8000,
        protocol="UDP",
    )
    yaml_body += "\n" + "\n".join(
        _make_np(name, dict(sel), port)
        for name, sel, port in np_check.DEFAULT_TARGETS[1:]
    )
    _write_yaml(f, yaml_body)
    fails = np_check.check_user_facing_np(str(f))
    assert len(fails) == 1
    assert "POD-side port 8000/TCP" in fails[0]


def test_main_clean_path_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    f = tmp_path / "rendered.yaml"
    _write_yaml(f, _three_targets_yaml())
    rc = np_check.main(["check", str(f)])
    out = capsys.readouterr()
    assert rc == 0
    assert "OK: 3 user-facing pod(s)" in out.out


def test_main_fail_path_exits_one_and_lists_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    f = tmp_path / "rendered.yaml"
    # render only 2 of 3 targets so mlflow is missing.
    yaml_body = "\n".join(
        _make_np(name, dict(sel), port)
        for name, sel, port in np_check.DEFAULT_TARGETS[:2]
    )
    _write_yaml(f, yaml_body)
    rc = np_check.main(["check", str(f)])
    out = capsys.readouterr()
    assert rc == 1
    assert "mlflow-ingress-allow" in out.err
    assert "troubleshooting.md" in out.err


def test_main_missing_file_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = np_check.main(["check", str(tmp_path / "absent.yaml")])
    out = capsys.readouterr()
    assert rc == 2
    assert "rendered YAML missing" in out.err


def test_main_no_args_prints_usage(capsys: pytest.CaptureFixture) -> None:
    rc = np_check.main([])
    out = capsys.readouterr()
    assert rc == 2
    assert "usage" in out.err


def test_main_unknown_verb_exits_two(capsys: pytest.CaptureFixture) -> None:
    rc = np_check.main(["wat"])
    out = capsys.readouterr()
    assert rc == 2
    assert "unknown verb" in out.err


def test_main_check_no_path_exits_two(capsys: pytest.CaptureFixture) -> None:
    rc = np_check.main(["check"])
    out = capsys.readouterr()
    assert rc == 2
    assert "usage" in out.err
