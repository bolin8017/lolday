from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.detector import DetectorBuildStatus, DetectorVersionStatus


class DetectorCreate(BaseModel):
    git_url: str
    name: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$|^[a-z0-9]$")
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
    config_schema: dict[str, Any]


class BuildCreate(BaseModel):
    # The tag travels into the BuildKit container's `buildctl build --output
    # name=...:<tag>` argument. Even though build.py now uses the exec form
    # (no shell interpolation) this regex is a defence-in-depth — also the
    # exact subset Harbor's registry tag grammar allows (leading alnum or _,
    # then alnum + `._-`, ≤128 chars; we cap at 100 to stay under our own
    # slugified job-name budget).
    git_tag: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,99}$")


class BuildRead(BaseModel):
    """Note: intentionally excludes build_token — internal credential, not for
    client consumption."""

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
