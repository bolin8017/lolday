"""Shared fixtures for backend/tests/services/."""

import logging

import pytest


@pytest.fixture(autouse=True)
def _reenable_app_loggers():
    """Re-enable app.* loggers that alembic's fileConfig may have disabled.

    alembic calls ``logging.config.fileConfig(alembic.ini)`` with the stdlib
    default ``disable_existing_loggers=True``. That permanently sets
    ``logger.disabled = True`` on every already-imported logger, including
    ``app.services.gpu_signal`` and any other ``app.*`` logger imported before
    the alembic fixture runs.

    The ``disabled`` flag short-circuits inside ``Logger.handle()`` *before*
    any handler runs, so ``caplog`` never sees records from those loggers —
    even though ``caplog.at_level()`` correctly attached the handler and set
    the level. The bug is order-dependent: it only manifests when an alembic
    migration fixture runs before this test under pytest-randomly's random
    ordering.

    Per .claude/rules/testing.md rule 4, the correct fix is to fix the fixture
    leak, not pin test order. We save and restore ``disabled`` around every
    test so the fixture is unconditionally safe regardless of run order.

    Precedent: backend/tests/test_services_notify.py::test_post_webhook_500_logs_host_not_url
    (commit 3815217, P3) uses the same inline save/restore pattern. This
    autouse fixture centralises the same logic for all services tests.
    """
    app_loggers = [
        name
        for name, obj in logging.Logger.manager.loggerDict.items()
        if name.startswith("app.") and isinstance(obj, logging.Logger)
    ]
    saved = {name: logging.getLogger(name).disabled for name in app_loggers}
    for name in app_loggers:
        logging.getLogger(name).disabled = False
    yield
    for name, was_disabled in saved.items():
        logging.getLogger(name).disabled = was_disabled
