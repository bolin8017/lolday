import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.job import JobStatus, JobType, ResourceProfile


class JobCreate(BaseModel):
    type: JobType
    detector_version_id: uuid.UUID
    train_dataset_id: uuid.UUID | None = None
    test_dataset_id: uuid.UUID | None = None
    predict_dataset_id: uuid.UUID | None = None
    source_model_version_id: uuid.UUID | None = None
    params: dict[str, Any] = {}
    resource_profile: ResourceProfile = ResourceProfile.STANDARD

    @model_validator(mode="after")
    def _validate_refs_per_type(self) -> "JobCreate":
        if self.type == JobType.TRAIN:
            if self.train_dataset_id is None:
                raise ValueError("train_dataset_id required for type=train")
            if self.source_model_version_id is not None:
                raise ValueError("source_model_version_id must be null for type=train")
            if self.predict_dataset_id is not None:
                raise ValueError("predict_dataset_id must be null for type=train")
        elif self.type == JobType.EVALUATE:
            if self.test_dataset_id is None:
                raise ValueError("test_dataset_id required for type=evaluate")
            if self.source_model_version_id is None:
                raise ValueError("source_model_version_id required for type=evaluate")
            if self.train_dataset_id is not None or self.predict_dataset_id is not None:
                raise ValueError("only test_dataset_id allowed for type=evaluate")
        elif self.type == JobType.PREDICT:
            if self.predict_dataset_id is None:
                raise ValueError("predict_dataset_id required for type=predict")
            if self.source_model_version_id is None:
                raise ValueError("source_model_version_id required for type=predict")
            if self.train_dataset_id is not None or self.test_dataset_id is not None:
                raise ValueError("only predict_dataset_id allowed for type=predict")
        return self


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: JobType
    status: JobStatus
    detector_version_id: uuid.UUID
    owner_id: uuid.UUID
    mlflow_run_id: str | None
    k8s_job_name: str | None
    failure_reason: str | None
    submitted_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary_metrics: dict[str, Any] | None = (
        None  # phase 11e — reconciler-projected read model
    )


class JobRead(JobSummary):
    train_dataset_id: uuid.UUID | None
    test_dataset_id: uuid.UUID | None
    predict_dataset_id: uuid.UUID | None
    source_model_version_id: uuid.UUID | None
    resolved_config: dict
    user_params: dict | None = None  # phase 13b B3
    # phase 13b Q1: per-stage parameter defaults extracted from the detector
    # manifest at response build time (not persisted on Job). Powers the
    # frontend's "(default)" muted-text vs override-bold visual on the
    # job-detail UserParamsTable. ``None`` when the stored manifest declares
    # no defaults — distinct from ``{}`` (which the UI would render as
    # "every row is an override").
    detector_defaults: dict[str, Any] | None = None
    # Cutover v0.16.1: surface the detector manifest's [output].positive_class
    # so the job detail UI can tag the positive row in PerClassMetrics and
    # bias PredictionSummaryCard ordering. ``None`` when the manifest does
    # not declare it (non-binary task or pre-schema_version=2 detectors).
    positive_class: str | None = None
    log_tail: str | None
    resource_profile: ResourceProfile
    mlflow_experiment_id: str | None


class JobList(BaseModel):
    items: list[JobSummary]
    total: int
    page: int
    page_size: int


class JobInternalConfig(BaseModel):
    yaml: str
    train_csv: str | None
    test_csv: str | None
    predict_csv: str | None
