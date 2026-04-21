"""Runtime validator for the lolday build pipeline.

Runs inside the ``validate`` init container of a detector build Job. It:

1. **AST-scans** the cloned repo to locate the ``BaseDetector`` subclass
   and the ``config_class`` it references — no imports yet.
2. Installs only the minimum dependencies required to instantiate the
   config schema (``islab-malware-detector`` + ``pydantic`` +
   ``pydantic-settings``) into a venv. **Does NOT install the detector
   repo itself**, and does NOT install its dependencies.
3. Loads the config module file directly via
   ``importlib.util.spec_from_file_location`` — bypasses the detector
   package's ``__init__.py`` so a heavy ``detector.py`` (e.g. one that
   imports ``torch``) never executes during validation.
4. Extracts the Pydantic ``config_class.model_json_schema()``.
5. POSTs the schema + git_sha back to the lolday backend
   (``/api/v1/internal/builds/{id}/schema``).

Why this shape: a naïve ``uv pip install <repo>`` of a torch-based
detector pulls ~7 GiB of nvidia-cu12 wheels into ``/tmp`` just to reach
line 1 of ``config.py`` — which is absurd since the schema extraction
only needs pydantic. The old design blew through every /tmp and memory
limit we tried (512 Mi → 12 Gi). The AST path keeps validate well under
256 Mi regardless of what the detector runtime depends on.

**Convention for detector authors:** the module containing
``config_class`` (typically ``<pkg>/config.py``) must be importable
with only ``maldet`` + ``pydantic`` on the path. No ``import torch``,
no ``import sklearn`` at the top of ``config.py``. Put heavy imports in
``detector.py`` where they belong.
"""

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx


class ValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def main() -> int:
    if len(sys.argv) < 2:
        return _fail("usage", "maldet_validator <repo_path>")
    repo = Path(sys.argv[1])
    if not repo.is_dir():
        return _fail("repo_missing", f"not a directory: {repo}")

    try:
        detector_info = _discover_via_ast(repo)
        _install_lightweight_deps()
        config_cls = _load_config_class(repo, detector_info)
        schema = config_cls.model_json_schema()
        git_sha = _read_git_sha(repo.parent / "git-sha")
        _post_schema(schema, git_sha)
        print(
            f"VALIDATION OK: detector={detector_info['detector_module']}."
            f"{detector_info['detector_class']} "
            f"config={detector_info['config_module_file']}:"
            f"{detector_info['config_class']}",
            flush=True,
        )
        return 0
    except ValidationError as e:
        return _fail(e.code, e.message)
    except Exception as e:
        return _fail("validation_error", f"{type(e).__name__}: {e}")


# ----------------------------------------------------------------- AST discovery


def _discover_via_ast(repo: Path) -> dict:
    """Return info needed to load config_class — no imports performed.

    Walks all ``.py`` under the repo (minus tests / hidden dirs), parses
    each, and finds:
      * a class whose bases include ``BaseDetector`` (directly imported
        or aliased from ``maldet``),
      * that class's ``config_class = X`` class-body assignment,
      * the import that resolves ``X`` to a module path.

    Returns: ``{detector_module, detector_class, config_class, config_module_file}``.
    """
    search_dirs = [repo]
    if (repo / "src").is_dir():
        search_dirs.append(repo / "src")

    for d in search_dirs:
        for py in d.rglob("*.py"):
            rel = py.relative_to(repo).parts
            if any(p.startswith(".") or p in {"tests", "test"} for p in rel):
                continue
            try:
                tree = ast.parse(py.read_text(errors="ignore"), filename=str(py))
            except SyntaxError:
                continue

            # Collect imports in this module so we can resolve `config_class = X`
            import_map = _build_import_map(tree)

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not _subclasses_base_detector(node, import_map):
                    continue
                config_ref = _find_config_class_attr(node)
                if config_ref is None:
                    continue
                # Resolve config_ref back to the source module file.
                config_module_name = import_map.get(config_ref)
                if config_module_name is None:
                    raise ValidationError(
                        "config_class_import_missing",
                        f"{py}: config_class={config_ref!r} has no matching import",
                    )
                config_module_file = _resolve_module_file(
                    search_dirs, config_module_name
                )
                if config_module_file is None:
                    raise ValidationError(
                        "config_module_file_missing",
                        f"cannot locate file for module {config_module_name!r} (from "
                        f"config_class={config_ref!r} in {py})",
                    )
                return {
                    "detector_module": _module_path_of(search_dirs, py),
                    "detector_class": node.name,
                    "config_class": config_ref,
                    "config_module_name": config_module_name,
                    "config_module_file": str(config_module_file),
                }

    raise ValidationError(
        "missing_base_detector",
        "no BaseDetector subclass with a config_class attribute found",
    )


def _build_import_map(tree: ast.Module) -> dict[str, str]:
    """Map local name → source module path for this file's imports.

    Example: ``from .config import UpxElfDetectorConfig`` →
    ``{"UpxElfDetectorConfig": ".config"}``. Relative imports keep their
    leading dots; the resolver turns them into absolute module paths in
    context later.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            prefix = "." * (node.level or 0) + (node.module or "")
            for alias in node.names:
                local = alias.asname or alias.name
                out[local] = prefix
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                out[local] = alias.name
    return out


def _subclasses_base_detector(cls: ast.ClassDef, import_map: dict[str, str]) -> bool:
    for base in cls.bases:
        name = _name_of(base)
        if name == "BaseDetector":
            source = import_map.get("BaseDetector", "")
            if "maldet" in source or source == "":
                return True
    return False


def _find_config_class_attr(cls: ast.ClassDef) -> str | None:
    """Return the RHS name in ``config_class = <Name>``, or None."""
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            tgt = stmt.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id == "config_class":
                return _name_of(stmt.value)
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id == "config_class" and stmt.value is not None:
                return _name_of(stmt.value)
    return None


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _resolve_module_file(search_dirs: list[Path], module_name: str) -> Path | None:
    """Find the .py file for a module, handling relative and absolute names."""
    # Relative like ".config" or "..constants" — search the tree for config.py
    basename = module_name.lstrip(".").split(".")[-1]
    if not basename:
        return None
    for d in search_dirs:
        for candidate in d.rglob(f"{basename}.py"):
            if any(
                p.startswith(".") or p in {"tests", "test"}
                for p in candidate.relative_to(d).parts
            ):
                continue
            return candidate
    return None


def _module_path_of(search_dirs: list[Path], py: Path) -> str:
    for d in search_dirs:
        try:
            rel = py.relative_to(d)
            parts = list(rel.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            return ".".join(parts)
        except ValueError:
            continue
    return py.stem


# ---------------------------------------------------------------- dep install


def _install_lightweight_deps() -> None:
    """Install only the deps needed for Pydantic schema extraction.

    Specifically NOT ``<repo>`` — we bypass its ``__init__.py`` entirely
    by loading ``config.py`` directly via ``spec_from_file_location``.
    """
    venv = Path("/tmp/venv")
    env = {**os.environ, "UV_CACHE_DIR": "/tmp/uv-cache", "VIRTUAL_ENV": str(venv)}
    subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True, env=env)
    proc = subprocess.run(
        [
            "uv", "pip", "install", "--no-cache-dir",
            "islab-malware-detector",
            "pydantic>=2",
            "pydantic-settings>=2",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise ValidationError("pip_install_failed", proc.stderr[-500:])
    site_pkgs = next(venv.glob("lib/python*/site-packages"))
    sys.path.insert(0, str(site_pkgs))


# ----------------------------------------------------------------- config load


def _load_config_class(repo: Path, info: dict):
    """Load the config module directly, bypass the detector package.

    Two tricks:

    1. We build a *fake* parent package (``_maldet_probe_pkg``) whose
       ``__path__`` points at the detector's source directory. That
       turns ``from .constants import X`` in ``config.py`` into a real
       Python relative import that the loader can resolve — while the
       real package's ``__init__.py`` (which typically imports
       ``detector.py`` and drags in torch / sklearn) is never executed.

    2. We use ``spec_from_file_location`` targeted at the config file
       directly; the loader only runs that file plus any relative
       siblings it transitively imports.
    """
    import types

    config_path = Path(info["config_module_file"])
    pkg_dir = config_path.parent
    pkg_name = "_maldet_probe_pkg"

    # Register a stub parent package so relative imports like
    # `from .constants import X` resolve. We do NOT run the real
    # package's __init__.py — that's the whole point.
    probe_pkg = types.ModuleType(pkg_name)
    probe_pkg.__path__ = [str(pkg_dir)]  # makes it a namespace package
    sys.modules[pkg_name] = probe_pkg

    module_fqname = f"{pkg_name}.config"
    spec = importlib.util.spec_from_file_location(
        module_fqname,
        config_path,
        submodule_search_locations=[str(pkg_dir)],
    )
    if spec is None or spec.loader is None:
        raise ValidationError(
            "config_spec_failed",
            f"cannot build import spec for {config_path}",
        )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_fqname] = mod
    try:
        spec.loader.exec_module(mod)
    except ImportError as e:
        # Detector author violated the "lightweight config.py" convention.
        raise ValidationError(
            "config_import_failed",
            f"loading {config_path.name} needs heavyweight deps ({e}); "
            "split heavy imports (torch, sklearn, etc.) out of config.py "
            "and into detector.py — lolday's validator only installs "
            "maldet + pydantic.",
        ) from e

    try:
        return getattr(mod, info["config_class"])
    except AttributeError as e:
        raise ValidationError(
            "config_class_attr_missing",
            f"{config_path} has no attribute {info['config_class']!r}",
        ) from e


# -------------------------------------------------------------------- transport


def _post_schema(schema: dict, git_sha: str) -> None:
    build_id = os.environ["BUILD_ID"]
    token = os.environ["BUILD_TOKEN"]
    url = os.environ["BACKEND_URL"] + f"/api/v1/internal/builds/{build_id}/schema"
    try:
        resp = httpx.post(
            url,
            json={"schema": schema, "git_sha": git_sha},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ValidationError("schema_post_failed", str(e))


def _read_git_sha(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text().strip()


def _fail(code: str, message: str) -> int:
    payload = {"validation_error": {"code": code, "message": message}}
    print(json.dumps(payload), flush=True, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
