"""Manifest validator + params-schema introspector for the lolday build pipeline.

Runs inside the ``validate`` init container of a detector build Job. It:

1. Parses ``maldet.toml`` via ``maldet.manifest.load_manifest`` (Pydantic
   ``DetectorManifest``). Fail-fast on missing or schema-invalid manifests.
2. Phase 11e: ``pip install`` the detector source so each stage's
   ``config_class`` import path resolves. For each stage whose
   ``params_schema`` is the empty placeholder ``{}``, calls
   ``cls.model_json_schema(mode="serialization")`` and patches the in-memory
   manifest with the derived JSON Schema. This is the single source of truth
   for ``params_schema`` — detector authors never hand-write or check in the
   schema, only the Pydantic config class.
3. Computes the five build-args (with the patched manifest) and writes them
   to ``/workspace/build-args/`` so the ``buildkit`` container can convert
   them to ``--opt build-arg`` flags. The args are split per-file (one
   ENV-style file per arg) to avoid shell-quoting bugs around the long
   base64 manifest string.
"""

from __future__ import annotations

import base64
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from maldet.manifest import DetectorManifest, ManifestNotFoundError, load_manifest
from pydantic import BaseModel

# Files written under build-args/.
ARG_NAMES = (
    "MALDET_NAME",
    "MALDET_VERSION",
    "MALDET_FRAMEWORK",
    "MALDET_MANIFEST_B64",
    "GIT_COMMIT",
)


class ValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def validate_manifest(repo: Path) -> DetectorManifest:
    """Return the parsed DetectorManifest, or raise ValidationError."""
    manifest_path = repo / "maldet.toml"
    if not manifest_path.is_file():
        raise ValidationError(
            "manifest_missing",
            f"maldet.toml not found at {manifest_path} (Phase 11c contract)",
        )
    try:
        manifest = load_manifest(manifest_path)
    except ManifestNotFoundError as exc:
        raise ValidationError("manifest_missing", str(exc)) from exc
    except Exception as exc:
        # Pydantic ValidationError, TOMLDecodeError, etc.
        raise ValidationError(
            "manifest_invalid", f"{type(exc).__name__}: {exc}"
        ) from exc
    # The pydantic schema constrains types only — empty / whitespace-only
    # strings here would still parse, then explode downstream as the OCI
    # registry tag or label payload. Reject them at validate time so the
    # detector author sees a clear error before the build-pipeline burns
    # cycles.
    if not manifest.detector.name.strip() or not manifest.detector.version.strip():
        raise ValidationError(
            "manifest_invalid",
            "detector.name and detector.version must be non-empty",
        )
    return manifest


_INSTALL_TARGET = Path("/tmp/maldet-validator-site-packages")


def install_detector(repo: Path) -> None:
    """``pip install`` the detector source into a tmp target dir so its modules
    are importable.

    Phase 11e: ``introspect_params_schemas`` needs to import each stage's
    ``config_class``; that requires the detector package to be on sys.path.

    The build-helper image runs as UID 1000 with no $HOME, so ``pip install``
    (default site-packages or ``--user``) hits "Permission denied: '/.local'".
    Use ``--target`` into ``/tmp`` (always writable) and prepend that dir to
    sys.path so the import sees the freshly-installed package.

    The build-helper image already has ``maldet[lightning]>=1.1`` preinstalled
    so torch/lightning don't need re-downloading; only the detector's own
    light deps (sklearn, pyelftools) hit the wire.
    """
    import site

    _INSTALL_TARGET.mkdir(parents=True, exist_ok=True)
    # ``--no-deps`` avoids fetching the detector's transitive deps (sklearn,
    # torch, lightning, etc) into ``--target``. The build-helper image already
    # has those in system site-packages via ``maldet[lightning]``. Without
    # ``--no-deps`` an elfcnndet-class detector pulls torch+CUDA into /tmp and
    # OOM-kills the validate container (exit 137).
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--quiet",
            "--no-deps",
            "--target",
            str(_INSTALL_TARGET),
            str(repo),
        ],
        check=True,
    )
    if str(_INSTALL_TARGET) not in sys.path:
        sys.path.insert(0, str(_INSTALL_TARGET))
    site.addsitedir(str(_INSTALL_TARGET))


def introspect_params_schemas(manifest: DetectorManifest) -> dict[str, Any]:
    """Return a manifest dict with ``params_schema`` populated per stage.

    For each stage whose ``params_schema`` is the placeholder empty dict,
    import the stage's ``config_class`` and replace ``params_schema`` with
    ``cls.model_json_schema(mode="serialization")``. Stages whose
    ``params_schema`` is already populated (e.g. detector author hand-wrote
    one) are left alone — placeholder is the only signal to introspect.
    """
    payload = manifest.model_dump(mode="json")
    for stage_name, stage in payload.get("stages", {}).items():
        if stage.get("params_schema"):
            continue  # already populated; trust author
        dotted = stage["config_class"]
        if ":" not in dotted:
            raise ValidationError(
                "config_class_invalid",
                f"[stages.{stage_name}.config_class] expected 'module:Class', got {dotted!r}",
            )
        mod_name, attr = dotted.split(":", 1)
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as exc:
            raise ValidationError(
                "config_class_unimportable",
                f"[stages.{stage_name}.config_class] cannot import {mod_name!r}: {exc}",
            ) from exc
        cls = getattr(mod, attr, None)
        if cls is None:
            raise ValidationError(
                "config_class_missing_attr",
                f"[stages.{stage_name}.config_class] {mod_name!r} has no attribute {attr!r}",
            )
        if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
            raise ValidationError(
                "config_class_not_basemodel",
                f"[stages.{stage_name}.config_class] {dotted} is not a pydantic.BaseModel subclass",
            )
        if cls.model_config.get("extra") != "forbid":
            raise ValidationError(
                "config_class_not_strict",
                f"[stages.{stage_name}.config_class] {dotted}: model_config['extra'] must be 'forbid'",
            )
        stage["params_schema"] = cls.model_json_schema(mode="serialization")
    return payload


def write_build_args(*, repo: Path, out: Path, git_sha_path: Path) -> None:
    """Compute the 5 build-args and write each to ``out/<NAME>``."""
    manifest = validate_manifest(repo)
    # Only pip install the detector if at least one stage actually needs
    # introspection (avoids the ~30s install hit in the trivial-already-populated
    # path used by build-helper unit tests).
    if any(not s.params_schema for s in manifest.stages.values()):
        install_detector(repo)
    payload = introspect_params_schemas(manifest)
    git_sha = git_sha_path.read_text().strip() if git_sha_path.is_file() else ""
    # ``model_dump(mode="json")`` already coerces every leaf to a JSON-native
    # type, so json.dumps should never see a non-serialisable value. Drop
    # ``default=str`` so a regression that lets a stray Path/datetime through
    # raises TypeError loudly instead of silently base64-encoding ``"PosixPath('...')"``
    # into the OCI label.
    manifest_b64 = base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    values = {
        "MALDET_NAME": manifest.detector.name,
        "MALDET_VERSION": manifest.detector.version,
        "MALDET_FRAMEWORK": manifest.detector.framework,
        "MALDET_MANIFEST_B64": manifest_b64,
        "GIT_COMMIT": git_sha,
    }
    for name in ARG_NAMES:
        (out / name).write_text(values[name])


def main() -> int:
    """``maldet_validator <repo_path> [<build_args_out>]``.

    The init-container invocation passes both paths; tests pass them too.
    """
    if len(sys.argv) < 2:
        return _fail("usage", "maldet_validator <repo_path> [<build_args_out>]")
    repo = Path(sys.argv[1])
    if not repo.is_dir():
        return _fail("repo_missing", f"not a directory: {repo}")

    out = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("/workspace/build-args")
    out.mkdir(parents=True, exist_ok=True)

    # The clone init container writes git-sha to /workspace/git-sha.
    git_sha_path = repo.parent / "git-sha"

    try:
        manifest = validate_manifest(repo)
        write_build_args(repo=repo, out=out, git_sha_path=git_sha_path)
        print(
            f"VALIDATION OK: name={manifest.detector.name} "
            f"version={manifest.detector.version} framework={manifest.detector.framework}",
            flush=True,
        )
        return 0
    except ValidationError as e:
        return _fail(e.code, e.message)
    except Exception as e:
        return _fail("validation_error", f"{type(e).__name__}: {e}")


def _fail(code: str, message: str) -> int:
    payload = {"validation_error": {"code": code, "message": message}}
    print(json.dumps(payload), flush=True, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
