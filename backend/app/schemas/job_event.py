"""Pydantic response schemas for job_events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ts: datetime
    kind: str
    payload: dict[str, Any]


class JobEventsPage(BaseModel):
    events: list[JobEventOut]
    next_since: datetime | None = None
