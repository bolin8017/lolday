"""Tests for validate_repo_static — manifest-driven (Phase 11c)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.services.validator import StaticValidationError, validate_repo_static


def _write_minimal_v2_repo(repo: Path, *, framework: str = "sklearn") -> None:
    (repo / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )
    (repo / "maldet.toml").write_text(textwrap.dedent(f"""
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
    """).strip() + "\n")


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
    (tmp_path / "maldet.toml").write_text('[detector]\nname = "x"\nversion = "1"\nframework = "sklearn"\n')
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
    (src / "__init__.py").write_text("from maldet import BaseDetector\nclass D(BaseDetector): pass\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "manifest_missing"
