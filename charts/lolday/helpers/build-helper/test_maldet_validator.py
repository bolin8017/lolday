"""Phase 11c manifest-driven validator tests."""

from __future__ import annotations

import base64
import json
import sys
import textwrap
from pathlib import Path

import pytest

# Tests live next to the validator script; import the module directly.
sys.path.insert(0, str(Path(__file__).parent))
import maldet_validator as mv  # noqa: E402


def _write_repo(root: Path, *, framework: str = "sklearn", name: str = "demo") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0.0"\nrequires-python = ">=3.12"\n'
    )
    (root / "maldet.toml").write_text(textwrap.dedent(f"""
        [detector]
        name = "{name}"
        version = "2.0.0"
        framework = "{framework}"

        [input]
        binary_format = "elf"
        dataset_contract = "sample_csv"

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
    src = root / "src" / name
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")


def test_validate_manifest_returns_parsed_manifest(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    m = mv.validate_manifest(tmp_path)
    assert m.detector.name == "demo"
    assert m.detector.framework == "sklearn"


def test_validate_manifest_raises_when_missing(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM x\n")
    with pytest.raises(mv.ValidationError, match="manifest_missing"):
        mv.validate_manifest(tmp_path)


def test_validate_manifest_raises_on_invalid_schema(tmp_path: Path) -> None:
    (tmp_path / "maldet.toml").write_text('[detector]\nname = "x"\n')  # missing many required fields
    with pytest.raises(mv.ValidationError, match="manifest_invalid"):
        mv.validate_manifest(tmp_path)


def test_write_build_args_emits_five_files(tmp_path: Path) -> None:
    _write_repo(tmp_path / "src", name="demo", framework="lightning")
    out = tmp_path / "build-args"
    out.mkdir()
    git_sha_path = tmp_path / "git-sha"
    git_sha_path.write_text("abc123def\n")

    mv.write_build_args(repo=tmp_path / "src", out=out, git_sha_path=git_sha_path)

    expected = {
        "MALDET_NAME": "demo",
        "MALDET_VERSION": "2.0.0",
        "MALDET_FRAMEWORK": "lightning",
        "GIT_COMMIT": "abc123def",
    }
    for key, val in expected.items():
        assert (out / f"{key}").read_text() == val
    # MANIFEST_B64 is a non-empty base64 of the JSON-serialized manifest
    b64 = (out / "MALDET_MANIFEST_B64").read_text()
    assert b64
    decoded = json.loads(base64.b64decode(b64))
    assert decoded["detector"]["name"] == "demo"


def test_write_build_args_missing_git_sha_uses_empty_string(tmp_path: Path) -> None:
    _write_repo(tmp_path / "src", name="demo")
    out = tmp_path / "build-args"
    out.mkdir()
    mv.write_build_args(repo=tmp_path / "src", out=out, git_sha_path=tmp_path / "absent")
    assert (out / "GIT_COMMIT").read_text() == ""


def test_main_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end through main(): valid repo + write-out → exit 0."""
    _write_repo(tmp_path)
    out = tmp_path / "build-args"
    out.mkdir()
    (tmp_path / "git-sha").write_text("deadbeef\n")
    monkeypatch.setattr(sys, "argv", ["maldet_validator", str(tmp_path), str(out)])
    rc = mv.main()
    assert rc == 0
    assert (out / "MALDET_NAME").read_text() == "demo"
