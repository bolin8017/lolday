"""WS /api/v1/jobs/{id}/events — live event stream.

Uses FastAPI's sync `TestClient` (Starlette-based) for the actual WebSocket
handshake since httpx.AsyncClient has no WS helper for ASGI apps. The DB
seed runs through the async `db_session` fixture; the TestClient then runs
the app on its own anyio portal thread.

The `event_broker` is module-level, so publishes from the test thread reach
subscribers inside the TestClient thread — but we must schedule the publish
on the TestClient's event loop so the enqueue lands on the coroutine that's
awaiting `queue.get()`. `client.portal.call` is the Starlette-provided bridge.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.db import get_async_session
from app.models import Detector, DetectorVersion, Job, User
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect


async def _seed_job_for_owner(session: AsyncSession, email: str) -> Job:
    """Create a Job owned by the named user (auto-create the user row)."""
    from sqlalchemy import select

    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        user = existing
    else:
        user = User(
            id=uuid.uuid4(),
            email=email,
        )
        session.add(user)
        await session.flush()

    det = Detector(
        name=f"ws-det-{uuid.uuid4().hex[:8]}",
        display_name="ws-det",
        owner_id=user.id,
        git_url="https://example.com/r.git",
    )
    session.add(det)
    await session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v1",
        git_sha="deadbeef",
        harbor_image="h/x:v1",
        image_digest="sha256:abc",
    )
    session.add(dv)
    await session.flush()
    job = Job(
        type="train",
        status="running",
        owner_id=user.id,
        detector_version_id=dv.id,
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
    )
    session.add(job)
    await session.commit()
    return job


def _make_test_client() -> TestClient:
    """Install the same auth + session overrides `user_client` uses, then
    return a sync TestClient. Mirrors `conftest._install_header_based_auth_override`
    but manually because that helper is wired to the async-client fixture."""
    from app.auth.cf_access import cf_access_user
    from app.main import app
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession as _AS

    from tests.conftest import test_session_maker

    async def session_override():
        async with test_session_maker() as session:
            yield session

    async def auth_override(
        request: Request,
        session: _AS = Depends(get_async_session),
    ) -> User:
        email = request.headers.get("x-test-user-email")
        if not email:
            raise HTTPException(401, "missing X-Test-User-Email (test fixture)")
        row = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(401, f"test fixture: user not seeded: {email}")
        return row

    app.dependency_overrides[get_async_session] = session_override
    app.dependency_overrides[cf_access_user] = auth_override
    return TestClient(app)


@pytest.mark.asyncio
async def test_ws_receives_broadcast_event(db_session: AsyncSession) -> None:
    """A subscriber over WS receives events published after `accept()`."""
    job = await _seed_job_for_owner(db_session, "user1@example.dev")

    from app.services.events_tail import event_broker

    client = _make_test_client()
    try:
        # Skip `with client:` — that would invoke the app's lifespan, which
        # (in main.py) tries to reach the real postgres host and DNS-fails
        # in the test sandbox. `websocket_connect` spins up its own portal
        # per session, reachable via `ws.portal`.
        with client.websocket_connect(
            f"/api/v1/jobs/{job.id}/events",
            headers={"x-test-user-email": "user1@example.dev"},
        ) as ws:
            event: dict[str, Any] = {
                "ts": "2026-04-24T00:00:00Z",
                "kind": "metric",
                "name": "loss",
                "value": 0.5,
            }
            # Schedule the publish on the WS session's app loop thread so
            # `put_nowait` lands on the queue the handler is awaiting.
            ws.portal.call(event_broker.publish, job.id, event)

            msg = ws.receive_json()
            assert msg["kind"] == "metric"
            assert msg["name"] == "loss"
            assert msg["value"] == 0.5
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ws_rejects_non_owner(db_session: AsyncSession) -> None:
    """A job owned by user1 must not stream to user2 — server closes the WS."""
    job = await _seed_job_for_owner(db_session, "user1@example.dev")
    # user2 must exist so the auth override resolves; then the handler's
    # owner check fires.
    from sqlalchemy import select

    existing = (
        await db_session.execute(select(User).where(User.email == "user2@example.dev"))
    ).scalar_one_or_none()
    if existing is None:
        db_session.add(
            User(
                id=uuid.uuid4(),
                email="user2@example.dev",
            )
        )
        await db_session.commit()

    client = _make_test_client()
    try:
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            client.websocket_connect(
                f"/api/v1/jobs/{job.id}/events",
                headers={"x-test-user-email": "user2@example.dev"},
            ) as ws,
        ):
            ws.receive_json()
        assert excinfo.value.code == 4403
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ws_rejects_unauthenticated(db_session: AsyncSession) -> None:
    """No X-Test-User-Email header -> WS closes with 4401."""
    job = await _seed_job_for_owner(db_session, "user1@example.dev")

    client = _make_test_client()
    try:
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            client.websocket_connect(
                f"/api/v1/jobs/{job.id}/events",
            ) as ws,
        ):
            ws.receive_json()
        assert excinfo.value.code == 4401
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ws_unknown_job_closes_4404(db_session: AsyncSession) -> None:
    """A job_id that doesn't exist -> WS closes with 4404."""
    # Seed the user so auth succeeds; then hit a bogus job_id.
    await _seed_job_for_owner(db_session, "user1@example.dev")
    fake_job_id = uuid.uuid4()

    client = _make_test_client()
    try:
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            client.websocket_connect(
                f"/api/v1/jobs/{fake_job_id}/events",
                headers={"x-test-user-email": "user1@example.dev"},
            ) as ws,
        ):
            ws.receive_json()
        assert excinfo.value.code == 4404
    finally:
        client.app.dependency_overrides.clear()
