"""Tests for validate_repo_static — manifest-driven (Phase 11c)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from app.services import validator as validator_mod
from app.services.validator import StaticValidationError, validate_repo_static


def _write_minimal_v2_repo(repo: Path, *, framework: str = "sklearn") -> None:
    (repo / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    (repo / "maldet.toml").write_text(
        textwrap.dedent(f"""
        [detector]
        name = "demo"
        version = "1.0.0"
        framework = "{framework}"

        [input]
        binary_format = "elf"

        [output]
        task = "binary_classification"
        classes = ["Malware", "Benign"]

        [resources]
        supports = ["cpu"]
        recommended = "cpu"

        [lifecycle]
        stages = ["train", "evaluate", "predict"]

        [artifacts]
        model = {{ path = "model/", type = "dir" }}
    """).strip()
        + "\n"
    )


def test_v2_repo_with_valid_maldet_toml_passes(tmp_path: Path) -> None:
    _write_minimal_v2_repo(tmp_path)
    validate_repo_static(tmp_path)  # must not raise


def test_missing_maldet_toml_raises_manifest_missing(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_missing"


def test_invalid_manifest_raises_manifest_invalid(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    # Missing required [output] section.
    (tmp_path / "maldet.toml").write_text(
        '[detector]\nname = "x"\nversion = "1"\nframework = "sklearn"\n'
    )
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_invalid"


def test_v0_repo_without_maldet_toml_no_longer_passes(tmp_path: Path) -> None:
    """Phase 11c removes the BaseDetector AST escape hatch — v0 detectors must fail."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    src = tmp_path / "src" / "demo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text(
        "from maldet import BaseDetector\nclass D(BaseDetector): pass\n"
    )
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_missing"


# ---------------------------------------------------------------------------
# Restored unit coverage for the per-file validators (size, pyproject,
# Dockerfile, maldet.toml). The earlier rewrite of this module dropped these,
# but the production code paths under ``_check_*`` are still wired into
# ``validate_repo_static``, so each one needs an explicit regression guard.
# ---------------------------------------------------------------------------


def _write_valid_repo(repo: Path) -> None:
    """Helper: minimal Phase 11c-compliant detector layout."""
    (repo / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    (repo / "maldet.toml").write_text(
        textwrap.dedent("""
        [detector]
        name = "demo"
        version = "1.0.0"
        framework = "sklearn"

        [input]
        binary_format = "elf"

        [output]
        task = "binary_classification"
        classes = ["Malware", "Benign"]

        [resources]
        supports = ["cpu"]
        recommended = "cpu"

        [lifecycle]
        stages = ["train", "evaluate", "predict"]

        [artifacts]
        model = { path = "model/", type = "dir" }
    """).strip()
        + "\n"
    )


def test_repo_too_large_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo whose total file size exceeds REPO_MAX_SIZE_BYTES must fail
    with code=repo_too_large before any per-file content validation runs."""
    monkeypatch.setattr(validator_mod, "REPO_MAX_SIZE_BYTES", 1024)
    _write_valid_repo(tmp_path)
    # Add a 2 KiB blob alongside the valid skeleton.
    (tmp_path / "blob.bin").write_bytes(b"x" * 2048)
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "repo_too_large"


def test_missing_pyproject_rejected(tmp_path: Path) -> None:
    """``pyproject.toml`` is mandatory — building the image without it would
    leave the detector author's deps un-pinned. Fail at submit time."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "maldet.toml").write_text(
        '[detector]\nname="d"\nversion="1"\nframework="sklearn"\n'
    )
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "pyproject_missing"


def test_unparseable_pyproject_rejected(tmp_path: Path) -> None:
    """Malformed TOML (unbalanced brackets) must surface as
    pyproject_unparseable rather than leaking the tomllib internal error."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text("[project\nname = broken\n")
    (tmp_path / "maldet.toml").write_text(
        '[detector]\nname="d"\nversion="1"\nframework="sklearn"\n'
    )
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "pyproject_unparseable"


def test_non_utf8_pyproject_rejected(tmp_path: Path) -> None:
    """A pyproject.toml that isn't valid UTF-8 (UTF-16 BOM here) would raise
    UnicodeDecodeError out of read_text — must be caught and surfaced as
    pyproject_unparseable instead of leaking as HTTP 500."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_bytes(b"\xff\xfe[project]\nname = 'x'\n")
    (tmp_path / "maldet.toml").write_text(
        '[detector]\nname="d"\nversion="1"\nframework="sklearn"\n'
    )
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "pyproject_unparseable"


def test_missing_dockerfile_rejected(tmp_path: Path) -> None:
    """Without a Dockerfile the buildkit container has nothing to build —
    fail closed with a clear code."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    (tmp_path / "maldet.toml").write_text(
        '[detector]\nname="d"\nversion="1"\nframework="sklearn"\n'
    )
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "dockerfile_missing"


def test_unparseable_maldet_toml_rejected(tmp_path: Path) -> None:
    """Malformed TOML inside ``maldet.toml`` must surface as
    manifest_unparseable, mirroring the pyproject path."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    (tmp_path / "maldet.toml").write_text("[detector\nname = broken\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_unparseable"


def test_non_utf8_maldet_toml_rejected(tmp_path: Path) -> None:
    """C1 fix: a maldet.toml whose bytes aren't valid UTF-8 used to raise
    UnicodeDecodeError as HTTP 500. Now it must surface as
    manifest_unparseable so the API contract stays stable.
    """
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    # UTF-16 BOM bytes are explicitly invalid UTF-8 (`\xff\xfe` is a stand-in
    # for any non-UTF-8 byte sequence in the manifest path).
    (tmp_path / "maldet.toml").write_bytes(b"\xff\xfe[detector]\nname = 'x'\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_unparseable"


def test_blank_detector_name_rejected(tmp_path: Path) -> None:
    """The pydantic ``DetectorManifest`` schema doesn't enforce min_length on
    detector.name/version, so empty strings would still validate then explode
    downstream. Reject them up front as manifest_invalid."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    (tmp_path / "maldet.toml").write_text(
        textwrap.dedent("""
        [detector]
        name = ""
        version = "1.0.0"
        framework = "sklearn"

        [input]
        binary_format = "elf"

        [output]
        task = "binary_classification"
        classes = ["Malware", "Benign"]

        [resources]
        supports = ["cpu"]
        recommended = "cpu"

        [lifecycle]
        stages = ["train", "evaluate", "predict"]

        [artifacts]
        model = { path = "model/", type = "dir" }
    """).strip()
        + "\n"
    )
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_invalid"


def test_blank_detector_version_rejected(tmp_path: Path) -> None:
    """Whitespace-only version must be rejected for the same reason as blank
    detector.name — would land as the OCI tag and break Harbor."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    (tmp_path / "maldet.toml").write_text(
        textwrap.dedent("""
        [detector]
        name = "demo"
        version = "   "
        framework = "sklearn"

        [input]
        binary_format = "elf"

        [output]
        task = "binary_classification"
        classes = ["Malware", "Benign"]

        [resources]
        supports = ["cpu"]
        recommended = "cpu"

        [lifecycle]
        stages = ["train", "evaluate", "predict"]

        [artifacts]
        model = { path = "model/", type = "dir" }
    """).strip()
        + "\n"
    )
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_invalid"
