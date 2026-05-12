"""Traefik ForwardAuth target for MLflow access control.

MLflow has no built-in authn. Browser users hit ``/mlflow/*`` via Cloudflare
Access (CF Access JWT in header). Job pods now go through Traefik too — the
T9 (H-12) NetworkPolicy restricts direct mlflow access to backend + Traefik
only. Traefik's ForwardAuth middleware makes every ``/mlflow/*`` request
hit this endpoint to authorise the call.

Traefik calls ``POST /api/v1/mlflow-authz`` with the original request's
headers (and ``X-Forwarded-Uri`` / ``X-Forwarded-Method`` extras). We return
200 to allow, 403 to deny. The MLflow Service is locked down by NetworkPolicy
so this is the only path that can reach MLflow.

Two principal types are accepted:

* Browser users (CF Access JWT in ``X-Forwarded-Cf-Access-Jwt-Assertion``
  header — Traefik forwards ``Cf-Access-Jwt-Assertion`` per
  ``authRequestHeaders``).
* Job pods (``Authorization: Bearer <job-token>`` — the same scheme as
  :func:`app.deps.require_job_token`).

For browser users we check the run's ``lolday.user_id`` tag (admin sees all).
For job pods we require the run_id (when derivable from URL) to match the
job's ``mlflow_run_id``.

Scope limit: only run_ids derivable from the URL path / query are checkable.
MLflow endpoints that pass ``run_id`` in the JSON body
(``update-run`` / ``log-metric`` / ``log-batch``) cannot be authorised here
because Traefik does not forward the body to ForwardAuth — those land in
the browser-path 403-on-unresolvable-run branch and in the job-token-path
403-on-mismatch branch. This is acceptable: maldet writes through
``/api/v1/internal/*`` (not directly to MLflow) and the browser UI rarely
POSTs run-mutating endpoints by URL-less bodies.
"""

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
from app.config import settings
from app.db import get_async_session
from app.models import Job, Role, User
from app.models.job import NON_TERMINAL_STATUSES

# NOTE: ``MlflowClient`` is imported as a module attribute (``_mc.MlflowClient``)
# rather than via ``from app.services.mlflow_client import MlflowClient`` so the
# test fixture's ``monkeypatch.setattr(mc, "MlflowClient", ...)`` is picked up by
# this router.  A ``from X import Y`` binding captures the class object at
# import time and is no longer affected when ``X.Y`` is reassigned later —
# tests would silently fall through to the real class even though the stub
# is meant to take over.  Resolving via ``_mc.MlflowClient`` per call follows
# the live module attribute and stays in lockstep with conftest's patching.
from app.services import mlflow_client as _mc
from app.services.job_tokens import hash_token
from app.services.mlflow_client import MlflowError

router = APIRouter()
logger = logging.getLogger(__name__)


# MLflow's REST paths we care about for ACL. The capture group ``run_id`` is
# the MLflow run_uuid (alphanumeric + underscore + dash, conservative match).
#
# The non-greedy ``runs/<verb>`` family (``runs/get`` / ``runs/search`` /
# ``runs/create`` / ``runs/update`` / ``runs/delete`` / ``runs/restore`` /
# ``runs/log-*`` / ``runs/set-tag`` / ``runs/delete-tag``) means
# ``[A-Za-z0-9_-]+`` would incorrectly capture the verb itself as a
# pseudo-run-id.  Reject those known endpoint names explicitly so the
# resolver falls through to ``QUERY_RUN_ID_RE`` (the verb endpoints all
# carry ``run_id`` in the body OR the query string; the body is unavailable
# to ForwardAuth, but ``?run_id=…`` covers ``runs/get``).
_RUNS_VERB_TOKENS = frozenset(
    {
        "get",
        "search",
        "create",
        "update",
        "delete",
        "restore",
        "set-tag",
        "delete-tag",
        "log-metric",
        "log-batch",
        "log-model",
        "log-parameter",
        "log-inputs",
    }
)
RUN_PATH_RE = re.compile(r"/api/2\.0/mlflow/runs/(?P<run_id>[A-Za-z0-9_-]+)")
ARTIFACT_PATH_RE = re.compile(
    r"/api/2\.0/mlflow-artifacts/artifacts/[^/]+/(?P<run_id>[A-Za-z0-9_-]+)/"
)
# Some MLflow endpoints carry the run_id in the query string instead of the
# path (e.g. ``GET /api/2.0/mlflow/runs/get?run_id=…``).  We pick it up via a
# permissive query-string scan so a URL like ``…/get?run_id=r-a&foo=bar`` is
# handled regardless of order.
QUERY_RUN_ID_RE = re.compile(r"[?&]run_id=(?P<run_id>[A-Za-z0-9_-]+)")


def _mlflow_client():
    """Construct an MlflowClient. Looked up dynamically so tests can stub it."""
    return _mc.MlflowClient(
        settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS
    )


async def _identify_via_cf(request: Request, session: AsyncSession) -> User | None:
    """Resolve a browser-driven request via CF Access JWT.

    Returns ``None`` on missing/invalid JWT (the caller then tries the
    job-token path before issuing a final 403).

    Test-mode shortcut: when ``ENVIRONMENT != "production"`` AND the test
    harness has overridden the ``cf_access_user`` dependency (i.e. we're
    inside the pytest fixture chain), honour an ``X-Test-User-Email``
    header.  Same pattern as the WS handler in ``routers/jobs.py`` —
    ForwardAuth bypasses the regular FastAPI dep chain by reading headers
    directly, so the existing test override does not propagate; this
    shortcut bridges the gap without leaking into production (gated by
    both the environment check AND the override-presence check).
    """
    from app.auth.cf_access import cf_access_user as _cf_access_user_dep
    from app.main import app as _app

    if (
        settings.ENVIRONMENT != "production"
        and _cf_access_user_dep in _app.dependency_overrides
    ):
        email = request.headers.get("x-test-user-email")
        if email:
            row = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            return row

    token = request.headers.get(
        "x-forwarded-cf-access-jwt-assertion"
    ) or request.headers.get("cf-access-jwt-assertion")
    if not token:
        return None
    try:
        return await resolve_user_from_jwt(session, token, log_context="mlflow-authz")
    except CfAccessAuthError:
        return None


async def _identify_via_job_token(
    request: Request, session: AsyncSession
) -> Job | None:
    """Resolve a job-pod-driven request via ``Authorization: Bearer <token>``.

    Returns ``None`` if the header is missing/malformed or the token does
    not match any non-terminal Job row.  Same hash + status filter as
    :func:`app.deps.require_job_token` so token revocation on job finalize
    flows through here automatically.
    """
    auth = request.headers.get("x-forwarded-authorization") or request.headers.get(
        "authorization"
    )
    if not auth or not auth.lower().startswith("bearer "):
        return None
    raw_token = auth[7:]
    h = hash_token(raw_token)
    job = (
        await session.execute(select(Job).where(Job.token_hash == h))
    ).scalar_one_or_none()
    if job is None or job.status not in NON_TERMINAL_STATUSES:
        return None
    return job


def _extract_run_id_from_url(uri: str) -> str | None:
    """Find a run_id in either the URI path or its query string.

    Matches order: ``ARTIFACT_PATH_RE`` (unambiguous, no verb collision),
    then ``RUN_PATH_RE`` filtered against the known runs verb tokens (so
    ``/runs/get`` does not become a pseudo-run-id ``get``), then a
    permissive ``?run_id=…`` query-string scan to cover endpoints like
    ``/runs/get?run_id=…`` where the id is not in the path segment.
    Returns ``None`` if no run_id is derivable — caller treats this as
    admin-only / scope-mismatch depending on principal type.
    """
    if not uri:
        return None
    m = ARTIFACT_PATH_RE.search(uri)
    if m:
        return m.group("run_id")
    m = RUN_PATH_RE.search(uri)
    if m and m.group("run_id") not in _RUNS_VERB_TOKENS:
        return m.group("run_id")
    m = QUERY_RUN_ID_RE.search(uri)
    if m:
        return m.group("run_id")
    return None


async def _run_owner_id(run_id: str) -> str | None:
    """Read the ``lolday.user_id`` tag from MLflow for a given run.

    Returns the owner UUID string, or ``None`` if the run is missing /
    untagged / MLflow is unreachable.  The caller treats ``None`` as a
    deny for non-admin browser users — runs without the tag are
    platform-internal (admin-only), matching the
    :func:`experiments_proxy._user_can_see_run_dict` policy.
    """
    try:
        run = await _mlflow_client().get_run(run_id)
    except MlflowError:
        logger.warning("mlflow-authz: get_run(%s) failed", run_id, exc_info=True)
        return None
    data = run.get("data") or {}
    tags_list = data.get("tags") or []
    # MLflow REST returns tags as ``[{"key": ..., "value": ...}, ...]``.
    # The autouse stub in tests/conftest.py::mock_mlflow returns ``tags`` as
    # a plain dict; tolerate both shapes so the same endpoint code path
    # works in CI and in production.
    if isinstance(tags_list, dict):
        return tags_list.get("lolday.user_id")
    tags = {t["key"]: t["value"] for t in tags_list if "key" in t}
    return tags.get("lolday.user_id")


@router.post("", include_in_schema=False)
async def mlflow_authz(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    x_forwarded_uri: Annotated[str, Header()] = "",
    x_forwarded_method: Annotated[str, Header()] = "GET",
) -> dict:
    """Decide whether the upstream MLflow request is allowed.

    200 with empty body = ALLOW; 403 = DENY. Traefik proxies the same
    status to the original caller.  The body of an ALLOW is informational
    only (``as: admin|user|job``); Traefik only consumes the status code
    and the ``authResponseHeaders`` list.
    """
    user = await _identify_via_cf(request, session)
    if user is not None:
        if user.role == Role.ADMIN:
            return {"allow": True, "as": "admin"}
        run_id = _extract_run_id_from_url(x_forwarded_uri)
        if run_id is None:
            # Endpoints we can't resolve to a run_id are admin-only.
            raise HTTPException(
                status_code=403, detail="cannot resolve run for ACL check"
            )
        owner = await _run_owner_id(run_id)
        if owner and owner == str(user.id):
            return {"allow": True, "as": "user", "run_id": run_id}
        raise HTTPException(status_code=403, detail="not run owner")

    job = await _identify_via_job_token(request, session)
    if job is not None:
        run_id = _extract_run_id_from_url(x_forwarded_uri)
        if run_id is None or run_id != job.mlflow_run_id:
            raise HTTPException(status_code=403, detail="job-token scope mismatch")
        return {"allow": True, "as": "job", "run_id": run_id}

    raise HTTPException(status_code=403, detail="no recognized auth")
