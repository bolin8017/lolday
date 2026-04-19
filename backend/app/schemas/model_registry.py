import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.model_registry import ModelVersionStage


class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mlflow_name: str
    mlflow_version: int
    mlflow_run_id: str
    current_stage: ModelVersionStage
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
    name: str
    latest_version: int | None
    latest_production_version: int | None
    latest_staging_version: int | None


class ModelTransitionRequest(BaseModel):
    to_stage: ModelVersionStage
    comment: str | None = None
