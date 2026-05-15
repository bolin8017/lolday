"""Tests for the Traefik ForwardAuth target ``/api/v1/mlflow-authz``.

The endpoint is the only authn/authz layer in front of MLflow once the
T9 (H-12) NetworkPolicy is applied — see
``backend/app/routers/mlflow_authz.py`` for context.

Coverage matrix:

* No auth header anywhere → 403.
* Browser owner (CF Access JWT path via test-mode header) → 200.
* Browser non-owner → 403.
* Browser admin (skips ACL) → 200.
* Browser, no resolvable run_id in URL → 403 (non-admin only).
* Job token, scope match → 200.
* Job token, scope mismatch → 403.
* Job token, terminal job → 403 (token revoked on finalize).
"""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
from app.models import Job, User
from app.models.job import JobStatus, JobType
from sqlalchemy import select

from tests.conftest import test_session_maker as _test_session_maker


async def _user_id_for_email(email: str) -> str:
    """Look up the UUID (as str) for a seeded test user."""
    async with _test_session_maker() as session:
        row = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
    return str(row.id)


def _run_get_response(run_id: str, owner_user_id: str) -> httpx.Response:
    """Build a stock GET /api/2.0/mlflow/runs/get response with the owner tag."""
    return httpx.Response(
        200,
        json={
            "run": {
                "info": {"run_id": run_id},
                "data": {
                    "metrics": [],
                    "params": [],
                    "tags": [
                        {"key": "lolday.user_id", "value": owner_user_id},
                    ],
                },
            }
        },
    )


# ---------------------------------------------------------------------------
# No auth → 403.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mlflow_authz_denies_no_auth(client) -> None:
    """Without any auth header, the endpoint must return 403."""
    r = await client.post(
        "/api/v1/mlflow-authz",
        headers={"X-Forwarded-Uri": "/api/2.0/mlflow/runs/get?run_id=r-a"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "no recognized auth"


# ---------------------------------------------------------------------------
# Browser path: owner / non-owner / admin / no-run-in-url.
# ---------------------------------------------------------------------------


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_mlflow_authz_allows_owner(user_client) -> None:
    """Browser request from the run's owner → 200."""
    uid = await _user_id_for_email("user1@example.dev")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=_run_get_response("r-a", uid)
        )

        r = await user_client.post(
            "/api/v1/mlflow-authz",
            headers={
                "X-Forwarded-Uri": "/api/2.0/mlflow/runs/r-a",
                "X-Forwarded-Method": "GET",
            },
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allow"] is True
    assert body["as"] == "user"
    assert body["run_id"] == "r-a"


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_mlflow_authz_denies_non_owner(second_user_client, user_client) -> None:
    """A browser request from a different user → 403.

    ``user_client`` is injected purely to seed user1@example.dev so we have a
    stable UUID for the owner tag; we then issue the request as user2.
    """
    uid_owner = await _user_id_for_email("user1@example.dev")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=_run_get_response("r-a", uid_owner)
        )

        r = await second_user_client.post(
            "/api/v1/mlflow-authz",
            headers={
                "X-Forwarded-Uri": "/api/2.0/mlflow/runs/r-a",
                "X-Forwarded-Method": "GET",
            },
        )

    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "not run owner"


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_mlflow_authz_admin_sees_all(auth_client_admin) -> None:
    """Admin bypasses the per-run ACL — no MLflow round-trip required."""
    # No respx setup: the endpoint should short-circuit admin → 200 before
    # touching MLflow. Assert no calls go out.
    async with respx.MockRouter(assert_all_called=False) as mock:
        r = await auth_client_admin.post(
            "/api/v1/mlflow-authz",
            headers={
                "X-Forwarded-Uri": "/api/2.0/mlflow/runs/r-a",
                "X-Forwarded-Method": "GET",
            },
        )
        # Admin must short-circuit without contacting MLflow.
        assert all(not route.called for route in mock.routes)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allow"] is True
    assert body["as"] == "admin"


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_mlflow_authz_denies_when_no_run_id_in_url(user_client) -> None:
    """Browser non-admin hitting an endpoint with no derivable run_id → 403.

    H-16: a GET request is used here so the method-allowlist check is not the
    gating factor; the denial comes from the unresolvable run_id branch.
    """
    r = await user_client.post(
        "/api/v1/mlflow-authz",
        headers={
            "X-Forwarded-Uri": "/api/2.0/mlflow/experiments/search",
            "X-Forwarded-Method": "GET",
        },
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "cannot resolve run for ACL check"


# ---------------------------------------------------------------------------
# Job-token path: scope match / mismatch / terminal-job revocation.
# ---------------------------------------------------------------------------


@pytest.fixture
async def _job_with_token(seed_user, seed_detector_version, client):
    """Insert a Job row with a known token and mlflow_run_id.

    Returns a tuple ``(job_id, raw_token, run_id)``.  The hash is stored on
    the Job row so the endpoint's bearer-token lookup succeeds.

    Also strips the inherited ``x-test-user-email`` header from ``client``
    so the job-token tests exercise the bearer-token path cleanly — without
    this, the ``seed_user`` → ``user_client`` chain leaves user1's email in
    the client headers and the endpoint's CF test-mode shortcut resolves
    that user instead of falling through to the job-token branch.
    """
    from app.services.job_tokens import generate_token, hash_token

    # Clear the inherited browser-identity header so requests against the
    # endpoint go through the job-token branch, not the CF test shortcut.
    client.headers.pop("x-test-user-email", None)

    raw_token = generate_token()
    h = hash_token(raw_token)
    run_id = "r-job-token-scope"
    dv_id_str = await seed_detector_version(name=f"jt-{uuid.uuid4().hex[:6]}")

    async with _test_session_maker() as session:
        job = Job(
            type=JobType.TRAIN,
            status=JobStatus.RUNNING,  # non-terminal so token is valid
            detector_version_id=uuid.UUID(dv_id_str),
            owner_id=seed_user.id,
            resolved_config={},
            mlflow_run_id=run_id,
            idempotency_key=uuid.uuid4().hex,
            token_hash=h,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return str(job.id), raw_token, run_id


@pytest.mark.asyncio
async def test_mlflow_authz_job_token_scope_match(client, _job_with_token) -> None:
    """Job token + matching mlflow_run_id in the URL → 200."""
    _job_id, raw_token, run_id = _job_with_token

    r = await client.post(
        "/api/v1/mlflow-authz",
        headers={
            "Authorization": f"Bearer {raw_token}",
            "X-Forwarded-Uri": f"/api/2.0/mlflow/runs/{run_id}",
            "X-Forwarded-Method": "POST",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allow"] is True
    assert body["as"] == "job"
    assert body["run_id"] == run_id


@pytest.mark.asyncio
async def test_mlflow_authz_job_token_scope_mismatch(client, _job_with_token) -> None:
    """Job token but URL run_id differs from the job's mlflow_run_id → 403."""
    _job_id, raw_token, _run_id = _job_with_token

    r = await client.post(
        "/api/v1/mlflow-authz",
        headers={
            "Authorization": f"Bearer {raw_token}",
            "X-Forwarded-Uri": "/api/2.0/mlflow/runs/r-some-other-run",
            "X-Forwarded-Method": "POST",
        },
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "job-token scope mismatch"


@pytest.mark.asyncio
async def test_mlflow_authz_job_token_query_string_run_id(
    client, _job_with_token
) -> None:
    """Job token + matching run_id in query string (?run_id=…) → 200.

    Covers endpoints like ``GET /api/2.0/mlflow/runs/get?run_id=…`` where
    the path itself does not carry the id.
    """
    _job_id, raw_token, run_id = _job_with_token

    r = await client.post(
        "/api/v1/mlflow-authz",
        headers={
            "Authorization": f"Bearer {raw_token}",
            "X-Forwarded-Uri": f"/api/2.0/mlflow/runs/get?run_id={run_id}",
            "X-Forwarded-Method": "GET",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["as"] == "job"


@pytest.mark.asyncio
async def test_mlflow_authz_job_token_terminal_job_rejected(
    client, _job_with_token
) -> None:
    """Once the Job is in a terminal status, the bearer token is rejected.

    Mirrors :func:`app.deps.require_job_token`'s H-20 behaviour: a
    completed job's lingering ``token_hash`` row should not grant access.
    """
    job_id, raw_token, run_id = _job_with_token

    # Flip status to a terminal value to simulate finalize / cleanup.
    async with _test_session_maker() as session:
        from sqlalchemy import update as sa_update

        await session.execute(
            sa_update(Job)
            .where(Job.id == uuid.UUID(job_id))
            .values(status=JobStatus.SUCCEEDED, finished_at=datetime.now(UTC))
        )
        await session.commit()

    r = await client.post(
        "/api/v1/mlflow-authz",
        headers={
            "Authorization": f"Bearer {raw_token}",
            "X-Forwarded-Uri": f"/api/2.0/mlflow/runs/{run_id}",
            "X-Forwarded-Method": "POST",
        },
    )
    # Token rejected → falls all the way through to the final 403.
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "no recognized auth"


# ---------------------------------------------------------------------------
# Artifact path: run_id is in the URL after the experiment_id segment.
# ---------------------------------------------------------------------------


@pytest.mark.no_mock_mlflow
@pytest.mark.asyncio
async def test_mlflow_authz_artifact_path_match(user_client) -> None:
    """``/api/2.0/mlflow-artifacts/artifacts/<exp>/<run>/...`` is recognised."""
    uid = await _user_id_for_email("user1@example.dev")
    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get("http://mlflow.lolday.svc:5000/api/2.0/mlflow/runs/get").mock(
            return_value=_run_get_response("r-a", uid)
        )

        r = await user_client.post(
            "/api/v1/mlflow-authz",
            headers={
                "X-Forwarded-Uri": "/api/2.0/mlflow-artifacts/artifacts/1/r-a/model.bin",
                "X-Forwarded-Method": "GET",
            },
        )

    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == "r-a"


# ---------------------------------------------------------------------------
# H-16: method allowlist — non-admin blocked on mutating methods.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mlflow_authz_non_admin_cannot_delete(user_client) -> None:
    """H-16: non-admin users get 403 on DELETE regardless of run ownership.

    The method-allowlist check fires before run-id resolution, so even a URL
    that would resolve to an owned run is denied at the method gate.
    The autouse mock_mlflow stub is active; no real MLflow round-trip occurs.
    """
    r = await user_client.post(
        "/api/v1/mlflow-authz",
        headers={
            "X-Forwarded-Uri": "/api/2.0/mlflow/runs/delete",
            "X-Forwarded-Method": "DELETE",
        },
    )
    assert r.status_code == 403, r.text
    assert "DELETE" in r.json()["detail"]


@pytest.mark.asyncio
async def test_mlflow_authz_non_admin_cannot_post(user_client) -> None:
    """H-16: non-admin users get 403 on POST mutation."""
    r = await user_client.post(
        "/api/v1/mlflow-authz",
        headers={
            "X-Forwarded-Uri": "/api/2.0/mlflow/runs/r-a",
            "X-Forwarded-Method": "POST",
        },
    )
    assert r.status_code == 403, r.text
    assert "POST" in r.json()["detail"]


@pytest.mark.asyncio
async def test_mlflow_authz_admin_can_delete(auth_client_admin) -> None:
    """H-16: admins are not restricted by the method allowlist."""
    r = await auth_client_admin.post(
        "/api/v1/mlflow-authz",
        headers={
            "X-Forwarded-Uri": "/api/2.0/mlflow/runs/delete",
            "X-Forwarded-Method": "DELETE",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allow"] is True
    assert body["as"] == "admin"


@pytest.mark.asyncio
async def test_mlflow_authz_job_token_can_post_to_own_run(
    client, _job_with_token
) -> None:
    """H-16: job tokens are NOT subject to the method allowlist.

    Sidecars need to write metrics/params for their own run. The bearer-token
    path skips the MUTATING_METHODS gate entirely.
    """
    _job_id, raw_token, run_id = _job_with_token

    r = await client.post(
        "/api/v1/mlflow-authz",
        headers={
            "Authorization": f"Bearer {raw_token}",
            "X-Forwarded-Uri": f"/api/2.0/mlflow/runs/{run_id}",
            "X-Forwarded-Method": "POST",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allow"] is True
    assert body["as"] == "job"
    assert body["run_id"] == run_id
