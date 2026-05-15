"""M-mlflow-stream: download_artifact streams via httpx.AsyncClient.stream + StreamingResponse.

Background: previously ``download_artifact`` issued ``r = await c.get(url);
return Response(content=r.content, ...)`` which buffers the full upstream
body in memory. A 500 MiB artifact materialises 500 MiB resident; under
concurrency the 512 MiB pod OOMKills. Security-hardening P6 / M-mlflow-stream
forbids this. Two changes verified here:

1. The handler must use ``httpx.AsyncClient.stream(...)`` + FastAPI
   ``StreamingResponse(...)``, not ``c.get(...)`` + ``Response(content=...)``.
2. A module-level ``asyncio.Semaphore(8)`` caps concurrent in-flight streams
   per pod, capping resident transit memory at ~2 MiB at saturation.
"""

import pytest
from app.models import User
from sqlalchemy import select

from tests.conftest import test_session_maker as _test_session_maker


async def _user1_id() -> str:
    """Return the seeded ``user1@example.dev`` row's UUID as a string."""
    async with _test_session_maker() as session:
        row = (
            await session.execute(select(User).where(User.email == "user1@example.dev"))
        ).scalar_one()
    return str(row.id)


@pytest.mark.no_mock_mlflow
async def test_download_artifact_streams_not_buffers(user_client, monkeypatch):
    """download_artifact must use httpx.stream + StreamingResponse, not r.content."""
    from unittest.mock import AsyncMock

    from app.routers import experiments_proxy

    uid = await _user1_id()

    # Mock the upstream MLflow get_run to return a run with the right artifact_uri.
    get_run_mock = AsyncMock(
        return_value={
            "info": {
                "run_id": "abc",
                "artifact_uri": "mlflow-artifacts:/exp/123/run/abc/artifacts",
            },
            "data": {"tags": [{"key": "lolday.user_id", "value": uid}]},
        }
    )
    # _client() returns a fresh MlflowClient each call; patch the class so
    # any instance returns our get_run mock.
    monkeypatch.setattr(
        experiments_proxy,
        "MlflowClient",
        lambda *a, **kw: type("_M", (), {"get_run": get_run_mock})(),
    )

    # Patch httpx.AsyncClient.stream so we can assert it's called (not .get).
    stream_used = {"called": False}

    class _FakeStream:
        status_code = 200
        headers = {"content-type": "application/octet-stream"}  # noqa: RUF012  # test stub

        async def aiter_bytes(self, chunk_size=65536):
            for _ in range(10):
                yield b"x" * 64 * 1024  # 640 KiB total, in 64 KiB chunks

        async def aread(self):
            return b""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def stream(self, method, url):
            stream_used["called"] = True
            assert method == "GET"
            return _FakeStream()

    monkeypatch.setattr(
        "app.routers.experiments_proxy.httpx.AsyncClient", _FakeAsyncClient
    )

    r = await user_client.get("/api/v1/runs/abc/artifacts/download?path=model.pkl")
    assert r.status_code == 200
    assert stream_used["called"] is True
    assert r.content.startswith(b"x" * 100)  # body is the streamed chunks concatenated
    # Content-Disposition still set (RFC 6266 helper from H-6).
    assert "model.pkl" in r.headers.get("content-disposition", "")


async def test_download_artifact_semaphore_caps_concurrency():
    """When 9 simultaneous downloads attempt the same stream, the 9th waits."""
    import asyncio

    from app.routers import experiments_proxy

    sem = experiments_proxy._MLFLOW_STREAM_SEM
    assert sem._value == 8  # default value at module load

    # Acquire all 8 permits.
    acquired = []
    for _ in range(8):
        await sem.acquire()
        acquired.append(True)
    assert sem._value == 0

    # 9th must block; check it's not instantly schedulable.
    task = asyncio.create_task(sem.acquire())
    done, _pending = await asyncio.wait({task}, timeout=0.05)
    assert not done, "Semaphore(8) did not block on 9th acquire"

    # Release one, the 9th should proceed.
    sem.release()
    await asyncio.wait_for(task, timeout=0.5)
    # Restore module state.
    for _ in range(8):
        sem.release()


@pytest.mark.no_mock_mlflow
async def test_download_artifact_upstream_error_returns_502(user_client, monkeypatch):
    """Non-2xx upstream must produce a 502 response, not a silent 200 with empty body.

    Regression guard for the fa0501f bug: raising HTTPException inside the
    StreamingResponse generator commits a 200 status first, so the client
    sees an empty 200 instead of the 502 detail. The fix moves the status
    check ahead of StreamingResponse construction.
    """
    from unittest.mock import AsyncMock

    from app.routers import experiments_proxy

    uid = await _user1_id()

    get_run_mock = AsyncMock(
        return_value={
            "info": {
                "run_id": "abc",
                "artifact_uri": "mlflow-artifacts:/exp/123/run/abc/artifacts",
            },
            "data": {"tags": [{"key": "lolday.user_id", "value": uid}]},
        }
    )
    monkeypatch.setattr(
        experiments_proxy,
        "MlflowClient",
        lambda *a, **kw: type("_M", (), {"get_run": get_run_mock})(),
    )

    class _FakeErrorStream:
        status_code = 503
        headers = {"content-type": "text/plain"}  # noqa: RUF012  # test stub

        async def aread(self):
            return b"upstream service unavailable"

        async def aiter_bytes(self, chunk_size=65536):
            # Should NOT be reached on the 503 path.
            if False:
                yield b""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def stream(self, method, url):
            return _FakeErrorStream()

    monkeypatch.setattr(
        "app.routers.experiments_proxy.httpx.AsyncClient", _FakeAsyncClient
    )

    r = await user_client.get("/api/v1/runs/abc/artifacts/download?path=model.pkl")
    assert r.status_code == 502, (
        "Non-2xx upstream must produce HTTP 502, not a silent degradation"
    )
    assert "upstream service unavailable" in r.text or "503" in r.text


@pytest.mark.no_mock_mlflow
async def test_download_artifact_releases_semaphore_on_upstream_error(
    user_client, monkeypatch
):
    """The semaphore must be released after a 502 from upstream error -- otherwise
    8 errors in a row would lock out subsequent legitimate streams."""
    from unittest.mock import AsyncMock

    from app.routers import experiments_proxy

    uid = await _user1_id()

    get_run_mock = AsyncMock(
        return_value={
            "info": {
                "run_id": "abc",
                "artifact_uri": "mlflow-artifacts:/exp/123/run/abc/artifacts",
            },
            "data": {"tags": [{"key": "lolday.user_id", "value": uid}]},
        }
    )
    monkeypatch.setattr(
        experiments_proxy,
        "MlflowClient",
        lambda *a, **kw: type("_M", (), {"get_run": get_run_mock})(),
    )

    class _FakeErrorStream:
        status_code = 503

        async def aread(self):
            return b"err"

        async def aiter_bytes(self, chunk_size=65536):
            if False:
                yield b""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def stream(self, method, url):
            return _FakeErrorStream()

    monkeypatch.setattr(
        "app.routers.experiments_proxy.httpx.AsyncClient", _FakeAsyncClient
    )

    sem = experiments_proxy._MLFLOW_STREAM_SEM
    initial = sem._value

    # Fire 10 doomed requests -- the semaphore must NOT permanently drain.
    for _ in range(10):
        r = await user_client.get("/api/v1/runs/abc/artifacts/download?path=model.pkl")
        assert r.status_code == 502

    # Allow event loop to settle any pending releases.
    import asyncio

    await asyncio.sleep(0.01)

    assert sem._value == initial, (
        f"Semaphore leaked: initial={initial}, final={sem._value}. "
        "Each error path must release the permit it acquired."
    )
