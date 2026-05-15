"""polyfactory factories for Detector, DetectorVersion, and DetectorBuild."""

from __future__ import annotations

from app.models.detector import Detector, DetectorBuild, DetectorVersion
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory


class DetectorFactory(SQLAlchemyFactory[Detector]):
    __model__ = Detector
    __set_relationships__ = False
    __set_foreign_keys__ = False


class DetectorVersionFactory(SQLAlchemyFactory[DetectorVersion]):
    __model__ = DetectorVersion
    __set_relationships__ = False
    __set_foreign_keys__ = False


class DetectorBuildFactory(SQLAlchemyFactory[DetectorBuild]):
    __model__ = DetectorBuild
    __set_relationships__ = False
    __set_foreign_keys__ = False
