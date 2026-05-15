"""polyfactory factory for Job."""

from __future__ import annotations

from app.models.job import Job, JobStatus
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory


class JobFactory(SQLAlchemyFactory[Job]):
    __model__ = Job
    __set_relationships__ = False
    __set_foreign_keys__ = False

    @classmethod
    def queued(cls, **kwargs) -> Job:
        return cls.build(status=JobStatus.QUEUED_BACKEND, **kwargs)

    @classmethod
    def running(cls, **kwargs) -> Job:
        return cls.build(status=JobStatus.RUNNING, **kwargs)

    @classmethod
    def succeeded(cls, **kwargs) -> Job:
        return cls.build(status=JobStatus.SUCCEEDED, **kwargs)

    @classmethod
    def failed(cls, **kwargs) -> Job:
        return cls.build(status=JobStatus.FAILED, **kwargs)
