"""Phase 11c manifest-driven validator for the lolday build pipeline.

Runs inside the ``validate`` init container of a detector build Job. It:

1. Parses ``maldet.toml`` via ``maldet.manifest.load_manifest`` (Pydantic
   ``DetectorManifest``). Fail-fast on missing or schema-invalid manifests.
2. Computes the five build-args and writes them to ``/workspace/build-args/``
   so the ``buildkit`` container can convert them to ``--opt build-arg``
   flags. The args are split per-file (one ENV-style file per arg) to avoid
   shell-quoting bugs around the long base64 manifest string.

There is no per-file shell parsing or AST scanning. The validator does not
install the detector repo; ``maldet[lightning] >= 1.0`` is preinstalled in
the build-helper image so the manifest module imports cleanly.

The validator does NOT run ``maldet check`` here, because that requires
``pip install`` of the detector repo (and pulls torch / sklearn). The
``buildkit`` container will fail loudly if any entrypoint dotted-path is
unreachable at container-startup time, surfacing the same class of error
without the install cost.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from maldet.manifest import DetectorManifest, ManifestNotFoundError, load_manifest

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


def write_build_args(*, repo: Path, out: Path, git_sha_path: Path) -> None:
    """Compute the 5 build-args and write each to ``out/<NAME>``."""
    manifest = validate_manifest(repo)
    git_sha = git_sha_path.read_text().strip() if git_sha_path.is_file() else ""
    # ``model_dump(mode="json")`` already coerces every leaf to a JSON-native
    # type, so json.dumps should never see a non-serialisable value. Drop
    # ``default=str`` so a regression that lets a stray Path/datetime through
    # raises TypeError loudly instead of silently base64-encoding ``"PosixPath('...')"``
    # into the OCI label.
    manifest_b64 = base64.b64encode(
        json.dumps(manifest.model_dump(mode="json"), separators=(",", ":")).encode(
            "utf-8"
        )
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
