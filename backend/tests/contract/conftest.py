"""Contract-tier fixtures: schemathesis app loader + respx replay-tape
loader. Contract tests run serial (one FastAPI instance per process —
schemathesis hits a single ASGI app and parallel workers would conflict).

Tests under backend/tests/contract/ carry @pytest.mark.contract.

OAS 3.1 note: FastAPI emits OpenAPI 3.1.0. schemathesis 3.x does not
fully support 3.1 natively, but provides an experimental down-converter
(schemathesis.experimental.OPEN_API_3_1) that remaps 3.1-only keywords
to 3.0 equivalents before loading. Enabled here once at module import.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import schemathesis
from fastapi.testclient import TestClient

# Enable experimental OAS 3.1 → 3.0 down-conversion before loading any
# schema.  Must be called before the first schemathesis.from_asgi() call.
schemathesis.experimental.OPEN_API_3_1.enable()

from app.main import app  # noqa: E402  # import after schemathesis env is set up

REPLAY_TAPE_DIR = Path(__file__).parent.parent / "fixtures" / "mlflow" / "recorded"


@pytest.fixture(scope="session")
def fastapi_app():
    """Return the FastAPI app instance. Tests can override deps as needed."""
    return app


@pytest.fixture(scope="session")
def schema(fastapi_app):
    """schemathesis schema loaded from the running app's /openapi.json."""
    return schemathesis.from_asgi("/openapi.json", fastapi_app)


@pytest.fixture
def client(fastapi_app) -> TestClient:
    return TestClient(fastapi_app)


@pytest.fixture
def mlflow_replay_tape(request):
    """Load a recorded MLflow response tape by file name (used by T17).

    Usage: parametrize with the tape filename, e.g.
        @pytest.mark.parametrize("mlflow_replay_tape", ["create_run.json"], indirect=True)
    """
    tape_name = getattr(request, "param", None)
    if not tape_name:
        return None
    with (REPLAY_TAPE_DIR / tape_name).open() as f:
        return json.load(f)
