"""Pydantic schemas for the model registry layer."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.model_registry import ModelVersionStage, ModelVersionVisibility


class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mlflow_version: int
    mlflow_run_id: str
    current_stage: ModelVersionStage
    visibility: ModelVersionVisibility
    detector_version_id: uuid.UUID
    source_job_id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime
    last_transitioned_at: datetime


class ModelVersionList(BaseModel):
    items: list[ModelVersionRead]
    total: int
    page: int
    page_size: int


class RegisteredModelSummary(BaseModel):
    """One row in `GET /api/v1/models`."""

    owner: str  # user.handle
    name: str  # detector.name
    description: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    latest_version: int | None = None
    latest_production_version: int | None = None
    latest_staging_version: int | None = None


class RegisteredModelRead(BaseModel):
    """Full detail for `GET /api/v1/models/{owner}/{name}`."""

    model_config = ConfigDict(from_attributes=True)

    owner: str
    name: str
    description: str | None
    tags: dict[str, str]
    latest_version: int | None
    latest_production_version: int | None
    latest_staging_version: int | None
    created_at: datetime


class RegisteredModelUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=5000)
    tags: dict[str, str] | None = None


class OwnerTransferRequest(BaseModel):
    new_owner_handle: str = Field(min_length=1, max_length=60)
    comment: str | None = Field(default=None, max_length=1000)


class ModelTransitionRequest(BaseModel):
    """Stage transition — schema unchanged from existing."""

    to_stage: ModelVersionStage
    comment: str | None = Field(default=None, max_length=1000)


class ModelVersionVisibilityUpdate(BaseModel):
    visibility: ModelVersionVisibility
    comment: str | None = Field(default=None, max_length=1000)
