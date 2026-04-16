from pathlib import Path

import pytest

from app.services.validator import StaticValidationError, validate_repo_static

FIXTURES = Path(__file__).parent / "fixtures"


def test_valid_detector_passes():
    validate_repo_static(FIXTURES / "valid_detector")


def test_missing_pyproject_rejected():
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(FIXTURES / "invalid_detector_no_pyproject")
    assert exc.value.code == "pyproject_missing"


def test_missing_dockerfile_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    (tmp_path / "detector.py").write_text("from maldet import BaseDetector\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "dockerfile_missing"


def test_missing_base_detector_import_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
    (tmp_path / "detector.py").write_text("class X: pass\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "base_detector_import_missing"


def test_unparseable_pyproject_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text("not-valid-toml = = = ")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "detector.py").write_text("from maldet import BaseDetector\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "pyproject_unparseable"


def test_non_utf8_pyproject_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_bytes(b"\xff\xfeinvalid")  # UTF-16-ish BOM
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "detector.py").write_text("from maldet import BaseDetector\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "pyproject_unparseable"


def test_repo_too_large_rejected(tmp_path, monkeypatch):
    from app.services import validator as validator_mod
    monkeypatch.setattr(validator_mod, "REPO_MAX_SIZE_BYTES", 100)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "detector.py").write_text("from maldet import BaseDetector\n")
    (tmp_path / "big.bin").write_bytes(b"x" * 200)
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "repo_too_large"
