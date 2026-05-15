"""polyfactory factory for DatasetConfig.

Note: app/models/dataset.py defines only DatasetConfig (there is no separate
Dataset model). The factory name follows the model name directly."""

from __future__ import annotations

from app.models.dataset import DatasetConfig
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory


class DatasetConfigFactory(SQLAlchemyFactory[DatasetConfig]):
    __model__ = DatasetConfig
    __set_relationships__ = False
    __set_foreign_keys__ = False
