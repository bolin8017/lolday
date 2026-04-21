"""Unit tests for maldet_validator AST discovery + config loader.

Runs outside the build-helper Docker image — drives the same module via
``pytest`` on a lightweight environment. Install with:

    pip install pytest "pydantic>=2" "pydantic-settings>=2" "islab-malware-detector>=0.5.0" httpx

Tests focus on the pure-Python bits (AST walk + spec_from_file_location
loader). The `_install_lightweight_deps` + `_post_schema` halves are
skipped — they do network / venv work that belongs in integration tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import maldet_validator as mv  # noqa: E402


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write a synthetic repo from a {relpath: source} map."""
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return tmp_path


_CONFIG_SRC = """\
from maldet.config import BaseDetectorConfig

class MyDetConfig(BaseDetectorConfig):
    seed: int = 42
"""

_DETECTOR_SRC = """\
from maldet import BaseDetector
from .config import MyDetConfig

class MyDet(BaseDetector):
    config_class = MyDetConfig
    def train(self): pass
    def evaluate(self): pass
    def predict(self): pass
"""

_INIT_HEAVY_SRC = """\
# Real-world detector __init__ eagerly imports the detector module
# (which in turn may import torch). The validator MUST bypass this.
from .detector import MyDet  # noqa: F401
"""


# ─────────────────────────────────────────────────────────────── AST discovery


def test_discover_src_layout(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {
        "src/mydet/__init__.py": _INIT_HEAVY_SRC,
        "src/mydet/detector.py": _DETECTOR_SRC,
        "src/mydet/config.py": _CONFIG_SRC,
    })
    info = mv._discover_via_ast(repo)
    assert info["detector_class"] == "MyDet"
    assert info["config_class"] == "MyDetConfig"
    assert Path(info["config_module_file"]).name == "config.py"


def test_discover_flat_layout(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {
        "mydet/__init__.py": "",
        "mydet/detector.py": _DETECTOR_SRC,
        "mydet/config.py": _CONFIG_SRC,
    })
    info = mv._discover_via_ast(repo)
    assert info["detector_class"] == "MyDet"


def test_discover_rejects_class_without_config_class_attr(tmp_path: Path) -> None:
    src = """\
from maldet import BaseDetector
class NoConfig(BaseDetector):
    def train(self): pass
"""
    repo = _make_repo(tmp_path, {
        "src/d/__init__.py": "",
        "src/d/detector.py": src,
    })
    with pytest.raises(mv.ValidationError) as e:
        mv._discover_via_ast(repo)
    assert e.value.code == "missing_base_detector"


def test_discover_rejects_unreferenced_config_class(tmp_path: Path) -> None:
    """class Foo(BaseDetector): config_class = Undefined — the name
    `Undefined` was never imported. Must raise, not silently skip."""
    src = """\
from maldet import BaseDetector
class Foo(BaseDetector):
    config_class = Undefined  # never imported
    def train(self): pass
"""
    repo = _make_repo(tmp_path, {
        "src/d/__init__.py": "",
        "src/d/detector.py": src,
    })
    with pytest.raises(mv.ValidationError) as e:
        mv._discover_via_ast(repo)
    assert e.value.code == "config_class_import_missing"


def test_discover_surfaces_syntax_error_with_file_and_line(tmp_path: Path) -> None:
    """A syntax error in the detector source must fail loudly with a
    file + line reference — NOT be silently skipped (the old behaviour
    fell through to 'missing_base_detector', which is actively misleading).
    """
    repo = _make_repo(tmp_path, {
        "src/d/__init__.py": "",
        "src/d/detector.py": "def ( bad syntax here",
    })
    with pytest.raises(mv.ValidationError) as e:
        mv._discover_via_ast(repo)
    assert e.value.code == "repo_syntax_error"
    assert "detector.py" in e.value.message


def test_discover_skips_tests_dir(tmp_path: Path) -> None:
    """A BaseDetector subclass under tests/ must NOT shadow the real one."""
    fake_test_detector = _DETECTOR_SRC.replace("class MyDet", "class TestDet")
    repo = _make_repo(tmp_path, {
        "src/mydet/__init__.py": "",
        "src/mydet/detector.py": _DETECTOR_SRC,
        "src/mydet/config.py": _CONFIG_SRC,
        "tests/test_stub.py": fake_test_detector,
    })
    info = mv._discover_via_ast(repo)
    assert info["detector_class"] == "MyDet"  # not TestDet


def test_resolve_module_file_rejects_ambiguous(tmp_path: Path) -> None:
    """Two config.py in the repo must raise config_module_ambiguous,
    not silently pick the first rglob match."""
    repo = _make_repo(tmp_path, {
        "src/a/__init__.py": "",
        "src/a/config.py": _CONFIG_SRC,
        "src/b/__init__.py": "",
        "src/b/config.py": _CONFIG_SRC,
    })
    search_dirs = [repo, repo / "src"]
    with pytest.raises(mv.ValidationError) as e:
        mv._resolve_module_file(search_dirs, ".config")
    assert e.value.code == "config_module_ambiguous"


# ───────────────────────────────────────────────────────────── config loader


@pytest.fixture(autouse=True)
def _cleanup_probe_pkg():
    """Drop the probe-pkg between tests so each `_load_config_class`
    call starts clean (the function itself guards against double-use,
    but the guard raises — this fixture lets multiple tests pass)."""
    yield
    sys.modules.pop("_maldet_probe_pkg", None)
    sys.modules.pop("_maldet_probe_pkg.config", None)


def test_load_config_bypasses_heavy_package_init(tmp_path: Path) -> None:
    """If __init__.py does `import torch` (heavy), the loader must still
    extract the config_class because spec_from_file_location skips the
    package __init__.
    """
    repo = _make_repo(tmp_path, {
        "src/mydet/__init__.py": "import nonexistent_heavy_dep  # would raise ImportError if executed",
        "src/mydet/detector.py": _DETECTOR_SRC,
        "src/mydet/config.py": _CONFIG_SRC,
    })
    info = mv._discover_via_ast(repo)
    cls = mv._load_config_class(repo, info)
    schema = cls.model_json_schema()
    assert "properties" in schema
    assert "seed" in schema["properties"]


def test_load_config_heavyweight_config_raises_config_import_failed(tmp_path: Path) -> None:
    """If config.py itself imports a heavy dep, the loader reports the
    actionable error code so the detector author can split imports."""
    bad_config = """\
import nonexistent_heavy_dep  # noqa: F401
from maldet.config import BaseDetectorConfig
class MyDetConfig(BaseDetectorConfig):
    seed: int = 1
"""
    repo = _make_repo(tmp_path, {
        "src/mydet/__init__.py": "",
        "src/mydet/detector.py": _DETECTOR_SRC,
        "src/mydet/config.py": bad_config,
    })
    info = mv._discover_via_ast(repo)
    with pytest.raises(mv.ValidationError) as e:
        mv._load_config_class(repo, info)
    assert e.value.code == "config_import_failed"


def test_syntax_error_in_repo_surfaces_during_discovery(tmp_path: Path) -> None:
    """Syntax error anywhere in the repo gets caught during AST walk
    with a ``repo_syntax_error`` code + filename:line reference — not
    silently skipped and not deferred to an obscure runtime error.
    """
    bad_config = "def ( bad syntax\n"
    repo = _make_repo(tmp_path, {
        "src/mydet/__init__.py": "",
        "src/mydet/detector.py": _DETECTOR_SRC,
        "src/mydet/config.py": bad_config,
    })
    with pytest.raises(mv.ValidationError) as e:
        mv._discover_via_ast(repo)
    assert e.value.code == "repo_syntax_error"
    assert "config.py" in e.value.message


def test_load_config_rejects_reentrant_use(tmp_path: Path) -> None:
    """Second call in same process must raise rather than silently reuse
    first detector's package path (which would mis-resolve relative imports)."""
    repo = _make_repo(tmp_path, {
        "src/mydet/__init__.py": "",
        "src/mydet/detector.py": _DETECTOR_SRC,
        "src/mydet/config.py": _CONFIG_SRC,
    })
    info = mv._discover_via_ast(repo)
    _ = mv._load_config_class(repo, info)
    with pytest.raises(mv.ValidationError) as e:
        mv._load_config_class(repo, info)
    assert e.value.code == "validator_not_reentrant"
