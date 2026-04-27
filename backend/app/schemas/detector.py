from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.detector import DetectorBuildStatus, DetectorVersionStatus


class DetectorCreate(BaseModel):
    git_url: str
    name: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$|^[a-z0-9]$"
    )
    display_name: str | None = None


class DetectorUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None


class DetectorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    display_name: str
    description: str | None
    git_url: str
    owner_id: UUID
    created_at: datetime


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    git_tag: str
    git_sha: str
    harbor_image: str
    image_digest: str
    built_at: datetime
    status: DetectorVersionStatus


class VersionDetailRead(VersionRead):
    pass


class BuildCreate(BaseModel):
    # git_tag is f-string-interpolated into the buildkit container's
    # ``sh -c`` args (services/build.py — ``--output type=image,name=...:<tag>``
    # and the cache-repo ref), so this regex is the direct first-line guard
    # against shell injection rather than defence-in-depth. The pattern
    # accepts the subset of Harbor's registry tag grammar we actually use
    # (leading alnum or underscore, then alnum + ``._-``, ≤128 chars; capped
    # at 100 to fit our slugified job-name budget).
    git_tag: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,99}$")


class BuildRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    detector_id: UUID
    git_tag: str
    git_sha: str | None
    status: DetectorBuildStatus
    failure_reason: str | None
    log_tail: str | None
    trivy_critical: int | None
    trivy_high: int | None
    started_at: datetime
    finished_at: datetime | None


class AvailableTag(BaseModel):
    name: str
    commit_sha: str
