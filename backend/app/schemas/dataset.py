import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.dataset import DatasetVisibility

_DATASET_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,99}$"


class DatasetConfigCreate(BaseModel):
    name: Annotated[
        str, Field(min_length=1, max_length=100, pattern=_DATASET_NAME_PATTERN)
    ]
    description: str | None = None
    visibility: DatasetVisibility = DatasetVisibility.PUBLIC
    csv_content: Annotated[str, Field(min_length=1)]

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty or whitespace-only")
        return v


class DatasetConfigUpdate(BaseModel):
    name: Annotated[
        str | None,
        Field(min_length=1, max_length=100, pattern=_DATASET_NAME_PATTERN),
    ] = None
    description: str | None = None
    visibility: DatasetVisibility | None = None


class DatasetConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    owner_id: uuid.UUID
    visibility: DatasetVisibility
    sample_count: int
    label_distribution: dict
    family_distribution: dict | None
    size_bytes: int
    csv_checksum: str
    created_at: datetime


class DatasetConfigList(BaseModel):
    items: list[DatasetConfigRead]
    total: int
    page: int
    page_size: int
