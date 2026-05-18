"""D3.3 — dev-mode seed response schema."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class SeededFixturesResponse(BaseModel):
    """Stable IDs for the dev-mode fixture set seeded by POST /dev/seed-fixtures."""

    detector_id: uuid.UUID
    detector_version_id: uuid.UUID
    train_dataset_id: uuid.UUID
    test_dataset_id: uuid.UUID
    queued_job_id: uuid.UUID
    registered_model_id: uuid.UUID
    model_version_id: uuid.UUID
    # Throwaway fixture — separate Detector + RegisteredModel +
    # ModelVersion that destructive Playwright specs
    # (`models/transfer-and-delete.spec.ts`) target so the primary
    # shared fixture above stays untouched by parallel runs.
    throwaway_detector_id: uuid.UUID
    throwaway_registered_model_id: uuid.UUID
    throwaway_model_version_id: uuid.UUID
