import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
from app.config import settings
from app.db import async_session_maker, get_async_session
from app.deps import get_mlflow, require_role
from app.metrics import BACKEND_ERRORS, PRIORITY_BUMP_TOTAL
from app.models import (
    DetectorVersion,
    Job,
    JobEvent,
    User,
)
from app.models.job import (
    NON_TERMINAL_STATUSES,
    JobStatus,
    JobType,
    assert_transition_legal,
)
from app.models.user import Role
from app.schemas.job import JobCreate, JobList, JobPatch, JobRead, JobSummary
from app.schemas.job_event import JobEventOut, JobEventsPage
from app.services.cluster_status import get_job_queue_position
from app.services.events_tail import event_broker
from app.services.job_submission import submit_job
from app.services.job_validation import ValidationError, validate_submission
from app.services.jobs_params_validate import resolve_detector_defaults
from app.services.k8s import (
    batch_v1,
    core_v1,
)
from app.services.mlflow_client import MlflowClient
from app.services.rate_limit import rate_limit_user
from app.users import current_active_user

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_job_read_with_defaults(job: Job, manifest: dict[str, Any] | None) -> JobRead:
    """Build a ``JobRead`` from ``job`` and attach ``detector_defaults``.

    Centralizes the response shape so all three ``/jobs/*`` endpoints that
    return ``JobRead`` (POST, GET-by-id, cancel) stay in lock-step. A future
    refactor of the manifest-defaults plumbing changes one place, not three.
    """
    read = JobRead.model_validate(job)
    read.detector_defaults = resolve_detector_defaults(manifest, job.type)
    output = (manifest or {}).get("output") or {}
    pc = output.get("positive_class")
    read.positive_class = pc if isinstance(pc, str) and pc else None
    return read


@router.post(
    "",
    status_code=202,
    response_model=JobRead,
    dependencies=[Depends(rate_limit_user("jobs_create", 30, 60))],
)
async def create_job(
    body: JobCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    client: Annotated[MlflowClient, Depends(get_mlflow)],
) -> JobRead:
    # Phase 2.4: maintenance mode short-circuit. Fires before any DB /
    # MLflow side-effect so the operator can flip the flag mid-cutover and
    # know no new submission can land in a half-wiped state. The frontend
    # detects 503 from job-submit to render a "platform under maintenance"
    # banner.
    if settings.BACKEND_MAINTENANCE_MODE:
        raise HTTPException(
            status_code=503,
            detail="maintenance: platform under maintenance, try again later",
            headers={"Retry-After": "3600"},
        )

    # D2.1 / R3: the body of POST /jobs is split into three pure services
    # so the validation surface can be exercised by hypothesis +
    # schemathesis without a TestClient. validate_submission does steps
    # 1-7 of the pre-R3 inline flow (detector / datasets / source-model /
    # manifest / stage / params / idempotency / concurrency / FK / spot-
    # check). submit_job does the MLflow experiment+run create, YAML
    # render, and the Job row insert. dispatch happens later out-of-band
    # via the FIFO scheduler reconciler.
    try:
        validated = await validate_submission(session, user, body)
        job = await submit_job(session, user, validated, client)
    except ValidationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message,
        ) from exc

    await session.commit()
    await session.refresh(job)
    return _build_job_read_with_defaults(job, validated.detector_version.manifest)


@router.get("", response_model=JobList)
async def list_jobs(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    type: JobType | None = None,
    status_: JobStatus | None = Query(None, alias="status"),
    detector_id: uuid.UUID | None = None,
) -> JobList:
    filters = []
    if user.role.value != "admin":
        filters.append(Job.owner_id == user.id)
    if type is not None:
        filters.append(Job.type == type)
    if status_ is not None:
        filters.append(Job.status == status_)
    if detector_id is not None:
        filters.append(
            Job.detector_version_id.in_(
                select(DetectorVersion.id).where(
                    DetectorVersion.detector_id == detector_id
                )
            )
        )

    count_stmt = select(func.count()).select_from(Job)
    if filters:
        count_stmt = count_stmt.where(and_(*filters))
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = (
        select(Job)
        .order_by(Job.submitted_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if filters:
        stmt = stmt.where(and_(*filters))
    items = (await session.execute(stmt)).scalars().all()

    return JobList(
        items=[JobSummary.model_validate(j) for j in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> JobRead:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=404, detail="job not found")
    # Phase 13b Q1: enrich JobRead with the per-stage manifest defaults so the
    # UserParamsTable can mark each row as override vs default. ``dv`` may
    # technically be ``None`` here (FK-violating delete) — fall through to
    # ``detector_defaults=None`` rather than 500.
    dv = await session.get(DetectorVersion, job.detector_version_id)
    return _build_job_read_with_defaults(job, dv.manifest if dv else None)


@router.get("/{job_id}/prediction-summary")
async def get_prediction_summary(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> dict:
    """Phase 13b B1: prediction summary cached on successful predict jobs.

    Cache miss returns 404; the reconciler projection populates the cache on
    terminal transition. Returning 404 (rather than recomputing on demand)
    keeps the read path predictable; legacy predict jobs without the cache
    need a one-shot backfill script.
    """
    job = await session.get(Job, job_id)
    if job is None or (job.owner_id != user.id and user.role.value != "admin"):
        raise HTTPException(status_code=404, detail="job not found")
    if job.type != JobType.PREDICT:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "not_predict_job",
                "message": "prediction-summary is only available on predict jobs",
            },
        )
    ps = (job.summary_metrics or {}).get("prediction_summary")
    if not ps:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "summary_unavailable",
                "message": "prediction summary not available for this job (legacy or failed)",
            },
        )
    return ps


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
):
    job = await session.get(Job, job_id)
    if job is None or (job.owner_id != user.id and user.role.value != "admin"):
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in NON_TERMINAL_STATUSES or job.finished_at is None:
        return await _stream_live_logs(job)
    age = datetime.now(UTC) - job.finished_at.replace(tzinfo=UTC)
    if age.total_seconds() > 86400:
        return Response(
            content=job.log_tail or "", status_code=410, media_type="text/plain"
        )
    return Response(content=job.log_tail or "", media_type="text/plain")


async def _stream_live_logs(job: Job):
    try:
        pods = await asyncio.to_thread(
            core_v1().list_namespaced_pod,
            namespace=settings.JOB_NAMESPACE,
            label_selector=f"lolday.job-id={job.id}",
        )
        if not pods.items:
            return Response(content="", media_type="text/plain")
        pod = pods.items[0]
        log = await asyncio.to_thread(
            core_v1().read_namespaced_pod_log,
            name=pod.metadata.name,
            namespace=settings.JOB_NAMESPACE,
            container="detector",
            tail_lines=1000,
        )
        return Response(content=log, media_type="text/plain")
    except Exception:
        BACKEND_ERRORS.labels(stage="job_logs_fetch").inc()
        logger.exception("job logs fetch failed", extra={"job_id": str(job.id)})
        return Response(
            content="(logs unavailable)", media_type="text/plain", status_code=503
        )


@router.get("/{job_id}/queue-position")
async def get_job_queue_position_endpoint(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
):
    job = await session.get(Job, job_id)
    if job is None or (job.owner_id != user.id and user.role.value != "admin"):
        raise HTTPException(status_code=404, detail="job not found")
    position = (
        await asyncio.to_thread(get_job_queue_position, job.k8s_job_name)
        if job.k8s_job_name
        else None
    )
    return {"position": position}


@router.post("/{job_id}/cancel", response_model=JobRead)
async def cancel_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> JobRead:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=403, detail="owner or admin only")
    if job.status not in NON_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"job already {job.status.value}")

    if job.k8s_job_name:
        try:
            await asyncio.to_thread(
                batch_v1().delete_namespaced_job,
                name=job.k8s_job_name,
                namespace=settings.JOB_NAMESPACE,
                propagation_policy="Background",
            )
        except Exception:
            BACKEND_ERRORS.labels(stage="cancel_k8s_cleanup").inc()
            logger.exception(
                "K8s job cleanup failed on cancel",
                extra={"job_id": str(job.id), "k8s_job_name": job.k8s_job_name},
            )

    prev_status = job.status
    assert_transition_legal(prev_status, JobStatus.CANCELLED)
    job.status = JobStatus.CANCELLED
    is_admin_cancel = job.owner_id != user.id
    job.failure_reason = (
        "cancelled_by_user" if not is_admin_cancel else "cancelled_by_admin"
    )
    job.finished_at = datetime.now(UTC)
    job.token_hash = None  # H-20: invalidate the init-container token on cancel
    # #166: admin-cancel is a forensically interesting cross-user mutation.
    # Capture before/after status. Self-cancel is the user's own action and
    # not audit-worthy.
    if is_admin_cancel:
        from app.services.audit import write_audit_log

        await write_audit_log(
            session,
            actor_id=user.id,
            action="job.cancel.admin",
            target_type="job",
            target_id=job.id,
            before={
                "status": prev_status.value,
                "owner_id": str(job.owner_id),
            },
            after={"status": job.status.value},
        )
    await session.commit()
    await session.refresh(job)
    # Same defensive ``dv if dv else None`` guard as ``get_job`` — the
    # detector version row is fetched fresh and could in theory be missing.
    dv = await session.get(DetectorVersion, job.detector_version_id)
    return _build_job_read_with_defaults(job, dv.manifest if dv else None)


@router.patch("/{job_id}", response_model=JobRead)
async def patch_job(
    job_id: uuid.UUID,
    body: JobPatch,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(require_role(Role.ADMIN))],
) -> JobRead:
    """Phase 6 (Task F) — admin-only priority bump for queued_backend jobs.

    Only ``priority`` is mutable. Rejects with 422 once the job has been
    submitted to Volcano (i.e. status != queued_backend), because the
    fifo_scheduler has already made its dispatch decision and the ordering
    can no longer be altered by changing the DB field alone.
    """
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != JobStatus.QUEUED_BACKEND:
        raise HTTPException(
            status_code=422,
            detail="priority cannot be changed after job has been submitted to Volcano",
        )
    if body.priority is not None and body.priority != job.priority:
        # Phase 6 follow-up A2: only count *changes*, not no-op patches, so
        # the rate genuinely reflects admin manual intervention. The
        # PRIORITY_BUMP_TOTAL counter then signals whether to invest in
        # auto-aging (spec §3.2 / follow-ups A2).
        PRIORITY_BUMP_TOTAL.inc()
        job.priority = body.priority
    await session.commit()
    await session.refresh(job)
    dv = await session.get(DetectorVersion, job.detector_version_id)
    return _build_job_read_with_defaults(job, dv.manifest if dv else None)


@router.get("/{job_id}/events", response_model=JobEventsPage)
async def list_job_events(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    since: datetime | None = None,
    since_id: uuid.UUID | None = None,
    limit: int = 500,
) -> JobEventsPage:
    """Paginate job events by a composite ``(ts, id)`` cursor.

    A naive ``ts > since`` filter skips events whose timestamp collides
    (ms-level ties land often under Volcano + fsync bursts). Ordering by
    ``(ts, id)`` and filtering by ``ts > since OR (ts = since AND id > since_id)``
    gives strict monotonicity without losing colliding events.
    """
    job = await session.get(Job, job_id)
    if job is None or (job.owner_id != user.id and user.role.value != "admin"):
        raise HTTPException(status_code=404, detail="job not found")
    stmt = select(JobEvent).where(JobEvent.job_id == job.id)
    if since is not None:
        if since_id is not None:
            stmt = stmt.where(
                or_(
                    JobEvent.ts > since,
                    and_(JobEvent.ts == since, JobEvent.id > since_id),
                )
            )
        else:
            stmt = stmt.where(JobEvent.ts > since)
    stmt = stmt.order_by(JobEvent.ts.asc(), JobEvent.id.asc()).limit(limit)
    rows = list(await session.scalars(stmt))
    if rows and len(rows) == limit:
        next_since = rows[-1].ts
        next_id = rows[-1].id
    else:
        next_since = None
        next_id = None
    return JobEventsPage(
        events=[JobEventOut.model_validate(r) for r in rows],
        next_since=next_since,
        next_id=next_id,
    )


async def _ws_session():
    """Yield an AsyncSession, honouring `get_async_session` overrides.

    WebSocket handlers don't participate in FastAPI's Depends() chain, so we
    look up `get_async_session` in `app.dependency_overrides` manually. Tests
    override it to point at SQLite; production leaves it alone and we fall
    back to the real `async_session_maker`.
    """
    from app.main import app as _app

    override = _app.dependency_overrides.get(get_async_session)
    if override is not None:
        # The override is an async generator function, matching the real
        # `get_async_session`; drive it the same way FastAPI's Depends does.
        gen = override()
        session = await gen.__anext__()
        return session, gen

    session = async_session_maker()
    await session.__aenter__()
    return session, session  # __aexit__ closes the session


async def _close_ws_session(holder) -> None:
    """Release a session obtained via `_ws_session`."""
    try:
        if hasattr(holder, "__anext__"):
            # Async-generator override: exhaust it so its `finally` runs.
            with contextlib.suppress(StopAsyncIteration):
                await holder.__anext__()
        elif hasattr(holder, "__aexit__"):
            await holder.__aexit__(None, None, None)
    except Exception:
        logger.debug("WS session close raised; ignoring", exc_info=True)


async def _resolve_user_from_ws(websocket: WebSocket) -> User | None:
    """Authenticate a WebSocket request.

    Mirrors the HTTP `cf_access_user` dep but works off `websocket.headers`:

    * Test-mode: honour `X-Test-User-Email` when the test harness has
      installed a `cf_access_user` override in `app.dependency_overrides`.
      WS handlers don't participate in the FastAPI dep chain, so we consult
      the override map directly rather than reading a magic env flag.
    * Production: verify `Cf-Access-Jwt-Assertion` via the shared
      `resolve_user_from_jwt` helper.

    Returns ``None`` only when the caller is *unauthenticated* — the caller
    closes the WS with RFC-6455 application code 4401. Database / connection
    errors are **not** swallowed; they propagate so the caller can distinguish
    "user is not logged in" from "our backend is broken" and close with a
    different code (4500).
    """
    from app.auth.cf_access import cf_access_user as _cf_access_user_dep
    from app.main import app as _app
    from app.middleware.csrf import origin_matches_host

    # #162 (CSWSH defense): browsers attach an ``Origin`` header to the WS
    # handshake but the SOP / CORS preflight does NOT apply to WebSockets.
    # If an attacker page on https://evil.example opens a WS to our backend,
    # the user's JWT cookie rides along on the handshake and the server
    # treats it as authenticated. Gate the handshake on
    # ``Origin host == Host`` the same way ``middleware/csrf.py`` does for
    # state-changing HTTP requests. Fail open when ``Origin`` is absent --
    # CLI / Python clients legitimately don't set it (mirrors the CSRF
    # middleware's non-browser fall-through).
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "")
    if origin is not None and not origin_matches_host(origin, host):
        return None
    session, holder = await _ws_session()
    try:
        if (
            settings.ENVIRONMENT != "production"
            and _cf_access_user_dep in _app.dependency_overrides
        ):
            email = websocket.headers.get("x-test-user-email")
            if not email:
                return None
            row = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            return row

        token = websocket.headers.get("cf-access-jwt-assertion")
        try:
            return await resolve_user_from_jwt(
                session, token, log_context="ws=/jobs/*/events"
            )
        except CfAccessAuthError:
            return None
    finally:
        await _close_ws_session(holder)


@router.websocket("/{job_id}/events")
async def websocket_job_events(
    websocket: WebSocket,
    job_id: uuid.UUID,
) -> None:
    """Push `JobEvent` records to the browser as the sidecar publishes them.

    RFC-6455 close codes (4401/4403/4404) mirror the HTTP status codes the
    paged GET returns; the frontend maps them back to user-facing messages.
    A 4500 is reserved for a backend error (DB/connection) during the
    auth/authz step — the frontend should treat this as "retry later," not
    "your token is wrong."
    """
    try:
        user = await _resolve_user_from_ws(websocket)
    except Exception:
        BACKEND_ERRORS.labels(stage="ws_auth").inc()
        logger.exception(
            "ws auth failed with unexpected error", extra={"job_id": str(job_id)}
        )
        await websocket.close(code=4500)
        return
    if user is None:
        await websocket.close(code=4401)
        return

    session, holder = await _ws_session()
    try:
        job = await session.get(Job, job_id)
        if job is None:
            await websocket.close(code=4404)
            return
        if job.owner_id != user.id and user.role.value != "admin":
            await websocket.close(code=4403)
            return
    finally:
        await _close_ws_session(holder)

    await websocket.accept()
    queue = event_broker.subscribe(job_id)
    try:
        while True:
            # Race the broker queue against client disconnect — without a
            # concurrent receive(), a disconnect is only detected on the
            # next send_json(), which may deadlock if no events flow.
            recv_task = asyncio.create_task(websocket.receive_text())
            get_task = asyncio.create_task(queue.get())
            done, pending = await asyncio.wait(
                {recv_task, get_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            if recv_task in done:
                # Client sent a frame or disconnected — in either case, stop.
                # (Any frame from the client counts as "I'm done" since this
                # is a one-way server->client stream.)
                break
            event = get_task.result()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        event_broker.unsubscribe(job_id, queue)
