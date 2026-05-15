"""polyfactory factory for User. Replaces the _make_user / seed_user
helpers from the original monolithic conftest (R1 refactor)."""

from __future__ import annotations

from app.models.user import Role, User
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory


class UserFactory(SQLAlchemyFactory[User]):
    __model__ = User
    __set_relationships__ = False
    __set_foreign_keys__ = False

    @classmethod
    def admin(cls, **kwargs) -> User:
        return cls.build(role=Role.ADMIN, **kwargs)

    @classmethod
    def developer(cls, **kwargs) -> User:
        return cls.build(role=Role.DEVELOPER, **kwargs)

    @classmethod
    def regular(cls, **kwargs) -> User:
        return cls.build(role=Role.USER, **kwargs)

    @classmethod
    def service_token(cls, **kwargs) -> User:
        return cls.build(role=Role.SERVICE_TOKEN, **kwargs)
