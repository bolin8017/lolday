"""Runtime validator for the lolday build pipeline.

Runs inside the ``validate`` init container of a detector build Job. It:

1. Installs the cloned detector repo as an editable package (+ islab-malware-detector).
2. Imports the detector's ``BaseDetector`` subclass via reflection.
3. Extracts the Pydantic ``config_class.model_json_schema()``.
4. POSTs the schema + git_sha back to the lolday backend
   (``/api/v1/internal/builds/{id}/schema``) using the build-scoped token.
5. Exits 0 on success, 1 with a structured error payload on stderr otherwise.

The script is intentionally dependency-light: stdlib + httpx + uv (bundled via
Dockerfile). It does not import any lolday code so it can be built and pushed
independently of backend releases.
"""

import importlib
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
        _pip_install(repo)
        cls = _discover_detector_class(repo)
        schema = cls.config_class.model_json_schema()
        git_sha = _read_git_sha(repo.parent / "git-sha")
        _post_schema(schema, git_sha)
        print(f"VALIDATION OK: {cls.__module__}.{cls.__name__}", flush=True)
        return 0
    except ValidationError as e:
        return _fail(e.code, e.message)
    except Exception as e:
        return _fail("validation_error", f"{type(e).__name__}: {e}")


def _pip_install(repo: Path) -> None:
    venv = Path("/tmp/venv")
    env = {**os.environ, "UV_CACHE_DIR": "/tmp/uv-cache", "VIRTUAL_ENV": str(venv)}
    subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True, env=env)
    proc = subprocess.run(
        [
            "uv", "pip", "install", "--no-cache-dir",
            str(repo), "islab-malware-detector",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise ValidationError("pip_install_failed", proc.stderr[-500:])
    # Add venv site-packages to import path so _discover_detector_class works
    site_pkgs = next(venv.glob("lib/python*/site-packages"))
    sys.path.insert(0, str(site_pkgs))


def _discover_detector_class(repo: Path):
    """Find the first module-level subclass of ``BaseDetector`` in the repo."""
    from maldet import BaseDetector

    # Support both flat layout (repo/pkg/__init__.py) and src layout (repo/src/pkg/__init__.py)
    search_dirs = [repo]
    if (repo / "src").is_dir():
        search_dirs.append(repo / "src")
    candidates = []
    for d in search_dirs:
        candidates.extend(
            p.name for p in d.iterdir()
            if p.is_dir() and (p / "__init__.py").is_file()
        )
    for pkg in candidates:
        try:
            mod = importlib.import_module(pkg)
        except Exception:
            continue
        for name in dir(mod):
            obj = getattr(mod, name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseDetector)
                and obj is not BaseDetector
            ):
                return obj
    raise ValidationError(
        "missing_base_detector",
        "no BaseDetector subclass found in repo modules",
    )


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
