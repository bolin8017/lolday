import tomllib
from pathlib import Path

import pydantic
from maldet.manifest import DetectorManifest

from app.config import settings

REPO_MAX_SIZE_BYTES = settings.REPO_MAX_SIZE_MB * 1024 * 1024


class StaticValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_repo_static(repo_root: Path) -> None:
    """Raise StaticValidationError on failure; return silently on success."""
    _check_size(repo_root)
    _check_pyproject(repo_root)
    _check_dockerfile(repo_root)
    _check_maldet_toml(repo_root)


def _check_size(repo_root: Path) -> None:
    total = 0
    for p in repo_root.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
            if total > REPO_MAX_SIZE_BYTES:
                raise StaticValidationError(
                    "repo_too_large",
                    f"repo exceeds {REPO_MAX_SIZE_BYTES} bytes",
                )


def _check_pyproject(repo_root: Path) -> None:
    pp = repo_root / "pyproject.toml"
    if not pp.is_file():
        raise StaticValidationError("pyproject_missing", "pyproject.toml not found")
    try:
        content = pp.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise StaticValidationError(
            "pyproject_unparseable", f"pyproject.toml is not valid UTF-8: {e}"
        ) from e
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as e:
        raise StaticValidationError(
            "pyproject_unparseable", f"pyproject.toml is not valid TOML: {e}"
        ) from e


def _check_dockerfile(repo_root: Path) -> None:
    if not (repo_root / "Dockerfile").is_file():
        raise StaticValidationError(
            "dockerfile_missing", "Dockerfile required at repo root"
        )


def _check_maldet_toml(repo_root: Path) -> None:
    """Phase 11c: the only "is this a detector repo?" signal is a parseable
    ``maldet.toml`` that satisfies the ``DetectorManifest`` schema."""
    manifest_path = repo_root / "maldet.toml"
    if not manifest_path.is_file():
        raise StaticValidationError(
            "manifest_missing",
            "maldet.toml required at repo root (Phase 11c contract)",
        )
    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise StaticValidationError(
            "manifest_unparseable",
            f"maldet.toml is not valid TOML: {e}",
        ) from e
    try:
        DetectorManifest.model_validate(data)
    except pydantic.ValidationError as e:
        raise StaticValidationError(
            "manifest_invalid",
            f"maldet.toml fails DetectorManifest schema: {e}",
        ) from e


# ---------------------------------------------------------------------------
# Phase 11b: job-submission pre-flight validators (unchanged in 11c)
# ---------------------------------------------------------------------------

from app.models.job import ResourceProfile  # noqa: E402

_PROFILE_TO_MANIFEST_TOKEN = {
    ResourceProfile.STANDARD: "cpu",
    ResourceProfile.GPU2: "gpu2",
}

SUPPORTED_DATASET_CONTRACTS = frozenset({"sample_csv"})


class JobSubmissionError(ValueError):
    """Raised when a job cannot be accepted given the detector's manifest."""


def validate_job_submission(
    *,
    manifest: DetectorManifest,
    resource_profile: ResourceProfile,
    dataset_contract: str,
    stage: str,
) -> None:
    token = _PROFILE_TO_MANIFEST_TOKEN.get(resource_profile)
    if token is None or token not in manifest.resources.supports:
        raise JobSubmissionError(
            f"resource_profile {resource_profile.value!r} (manifest token {token!r}) "
            f"not in detector.resources.supports={manifest.resources.supports}"
        )
    if dataset_contract != manifest.input.dataset_contract:
        raise JobSubmissionError(
            f"dataset_contract mismatch: platform sent {dataset_contract!r}, "
            f"detector expects {manifest.input.dataset_contract!r}"
        )
    if dataset_contract not in SUPPORTED_DATASET_CONTRACTS:
        raise JobSubmissionError(
            f"dataset_contract {dataset_contract!r} not supported by the platform; "
            f"supported: {sorted(SUPPORTED_DATASET_CONTRACTS)}"
        )
    if stage not in manifest.lifecycle.stages:
        raise JobSubmissionError(
            f"stage {stage!r} not declared in detector.lifecycle.stages={manifest.lifecycle.stages}"
        )
    if resource_profile == ResourceProfile.GPU2 and not manifest.lifecycle.supports_distributed:
        raise JobSubmissionError(
            f"resource_profile {resource_profile.value!r} allocates multiple GPUs but "
            f"detector's lifecycle.supports_distributed is "
            f"{manifest.lifecycle.supports_distributed!r}; set supports_distributed to "
            f"ddp/fsdp/deepspeed to accept multi-GPU jobs"
        )
