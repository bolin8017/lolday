import asyncio
import logging
import mimetypes
import weakref
from contextlib import AsyncExitStack
from pathlib import PurePosixPath
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.models import Role, User
from app.services.http_headers import build_content_disposition
from app.services.mlflow_client import MlflowClient, MlflowError
from app.users import current_active_user

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# H-1 / H-2: per-user ACL + artifact path-traversal guard.
#
# All five MLflow proxy handlers authenticate via ``current_active_user`` but
# previously returned data regardless of which lolday user owned the run.
# Every job-spawned run carries ``tags["lolday.user_id"]`` (set in
# ``routers/jobs.py`` at run creation). The ACL strategy: admin sees all;
# non-admin sees only runs whose ``lolday.user_id`` matches their UUID.
# Runs that lack the tag are treated as platform-internal (admin-only).
# ---------------------------------------------------------------------------


def _user_can_see_run(user: User, run_tags: dict[str, str]) -> bool:
    """Owner-or-admin check against the run's ``lolday.user_id`` tag.

    Returns True iff the caller is admin OR the tag matches the caller's
    UUID. Runs without the tag are treated as platform-internal (admin-only).
    """
    if user.role == Role.ADMIN:
        return True
    owner_id = run_tags.get("lolday.user_id")
    return owner_id is not None and owner_id == str(user.id)


def _user_can_see_run_dict(user: User, raw_run: dict) -> bool:
    """Same as :func:`_user_can_see_run` but works on the raw MLflow REST run shape."""
    data = raw_run.get("data") or {}
    tags_list = data.get("tags") or []
    tags = {t["key"]: t["value"] for t in tags_list if "key" in t}
    return _user_can_see_run(user, tags)


def _mlflow_user_filter(user_id: Any) -> str:
    """Build the MLflow ``filter_string`` that gates runs to a single user.

    §3.16 (security-hardening 2026-05-15 post-program review). MLflow's
    ``filter_string`` is not a parametrised query language -- it's
    interpolated into the upstream URL as-is. Today the value is a UUID
    so practically safe, but a future refactor that lets a non-UUID
    string land here would be a vector. Cast through ``str(uuid)`` and
    verify the UUID shape before interpolating; ValueError surfaces as
    a 500 (loud, not silent).
    """
    import uuid as _uuid

    if not isinstance(user_id, _uuid.UUID):
        try:
            user_id = _uuid.UUID(str(user_id))
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"_mlflow_user_filter rejects non-UUID user_id={user_id!r}"
            ) from e
    return f"tags.\"lolday.user_id\" = '{user_id!s}'"


def _validate_artifact_path(path: str) -> str:
    """Reject path-traversal and absolute paths in the user-supplied artifact path.

    Returns the path unchanged on success; raises :class:`HTTPException` (400)
    otherwise. The MLflow proxy interpolates ``path`` into the upstream URL,
    so unfiltered ``..`` segments allow cross-run artifact reads.
    """
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    if path.startswith("/") or path.startswith("\\"):
        raise HTTPException(status_code=400, detail="absolute path forbidden")
    parts = PurePosixPath(path).parts
    if any(p in (".", "..") for p in parts):
        raise HTTPException(status_code=400, detail="path traversal forbidden")
    return path


_stats_cache: TTLCache[str, dict] = TTLCache(maxsize=64, ttl=30)
# L-experiment-stats-lock (security-hardening P6): WeakValueDictionary means
# an entry is GC'd as soon as no caller still holds the Lock -- no per-
# experiment leak. Behaviourally equivalent to a plain dict for the
# _experiment_stats hot path because callers retain a local reference for
# the duration of the ``async with lock:`` block.
_stats_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


# M-mlflow-stream (security-hardening P6): cap concurrent MLflow artifact
# streams per backend pod. 8 x ~256 KiB transit buffer = 2 MiB peak resident,
# well under the 512 MiB pod limit even at saturation. See plan section D4
# for sizing validation.
_MLFLOW_STREAM_SEM: asyncio.Semaphore = asyncio.Semaphore(8)


def _client() -> MlflowClient:
    return MlflowClient(
        settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS
    )


def _flatten_run(
    r: dict[str, Any],
    *,
    lolday_job_meta: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    """MLflow REST returns runs as {info: {...}, data: {metrics: [{key,value}], ...}}.

    The frontend (and our aggregate logic) wants flat
    {run_id, status, metrics: {key: value}, params: {...}, tags: {...}}.
    Centralising the conversion here keeps the proxy contract simple and
    matches what callers already assume.

    ``lolday_job_meta`` (spec § 5.1 / § 6.8) maps the ``lolday.job_id`` tag
    value to its lolday-side ``Job.started_at`` / ``Job.finished_at`` ISO
    timestamps. The frontend Runs page renders Duration from those two so the
    column reflects compute time rather than wall-clock-from-submit (which is
    what MLflow's own ``info.start_time`` / ``info.end_time`` measure).
    """
    info = r.get("info") or {}
    data = r.get("data") or {}
    metrics_list = data.get("metrics") or []
    params_list = data.get("params") or []
    tags_list = data.get("tags") or []
    tags = {t["key"]: t["value"] for t in tags_list if "key" in t}
    out: dict[str, Any] = {
        "run_id": info.get("run_id") or info.get("run_uuid"),
        "run_name": info.get("run_name"),
        "experiment_id": info.get("experiment_id"),
        "status": info.get("status"),
        "start_time": info.get("start_time"),
        "end_time": info.get("end_time"),
        "artifact_uri": info.get("artifact_uri"),
        "lifecycle_stage": info.get("lifecycle_stage"),
        "metrics": {m["key"]: m["value"] for m in metrics_list if "key" in m},
        "params": {p["key"]: p["value"] for p in params_list if "key" in p},
        "tags": tags,
        "lolday_started_at": None,
        "lolday_finished_at": None,
    }
    job_id = tags.get("lolday.job_id")
    if lolday_job_meta and job_id and job_id in lolday_job_meta:
        out["lolday_started_at"] = lolday_job_meta[job_id]["started_at"]
        out["lolday_finished_at"] = lolday_job_meta[job_id]["finished_at"]
    return out


async def _fetch_lolday_job_meta(
    run_ids: list[str],
    session: AsyncSession,
) -> dict[str, dict[str, str | None]]:
    """Map lolday Job.id (string) → {started_at, finished_at} ISO strings.

    Lookup is by ``Job.mlflow_run_id`` IN (...) so it can batch any number of
    runs in one query. The frontend joins on the ``lolday.job_id`` tag, so the
    return-dict is keyed by ``Job.id`` as a string.
    """
    from app.models.job import Job

    if not run_ids:
        return {}
    stmt = select(Job).where(Job.mlflow_run_id.in_(run_ids))
    rows = (await session.execute(stmt)).scalars().all()
    return {
        str(j.id): {
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        }
        for j in rows
    }


@router.get("/experiments")
async def list_experiments(
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
    include: str | None = Query(None, pattern="^stats$"),
):
    try:
        experiments = await _client().search_experiments(max_results=max_results)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # H-1: per-experiment owner filter for non-admin callers — an experiment
    # is visible iff it has at least one run tagged with the caller's
    # ``lolday.user_id``. One ``search_runs`` per experiment is acceptable at
    # lab-scale (< 50 experiments).
    if user.role != Role.ADMIN:
        kept = []
        # §3.16: build the MLflow filter via a vetted helper. MLflow's
        # filter_string is not a parametrised query language; the only
        # safe interpolation is a value that has already been narrowed
        # to a known shape. UUID is the narrowest shape we can enforce.
        filter_string = _mlflow_user_filter(user.id)
        for exp in experiments:
            try:
                runs = await _client().search_runs(
                    experiment_ids=[exp["experiment_id"]],
                    max_results=1,
                    filter_string=filter_string,
                )
            except MlflowError:
                runs = []
            if runs:
                kept.append(exp)
        experiments = kept

    if include != "stats":
        return experiments

    enriched = []
    for exp in experiments:
        try:
            stats = await _experiment_stats(exp["experiment_id"])
        except MlflowError as e:
            # Stats failure shouldn't poison the whole list; degrade gracefully.
            logger.warning(
                "experiment_stats failed for %s: %s", exp["experiment_id"], e
            )
            stats = {"run_count": None, "best_f1": None, "latest_start_time": None}
        enriched.append({**exp, **stats})
    return enriched


async def _experiment_stats(experiment_id: str) -> dict:
    """Async TTL-cached aggregate. cachetools.@cached doesn't support async,
    so we cache by hand with a per-key Lock to avoid stampede."""
    if experiment_id in _stats_cache:
        return _stats_cache[experiment_id]
    lock = _stats_locks.setdefault(experiment_id, asyncio.Lock())
    async with lock:
        if experiment_id in _stats_cache:  # double-check after acquiring lock
            return _stats_cache[experiment_id]
        raw = await _client().search_runs([experiment_id], max_results=1000)
        runs = [_flatten_run(r) for r in raw]
        f1s = [r["metrics"].get("f1") for r in runs if r.get("status") == "FINISHED"]
        f1s = [x for x in f1s if x is not None]
        result = {
            "run_count": len(runs),
            "best_f1": max(f1s) if f1s else None,
            "latest_start_time": max(
                (r["start_time"] for r in runs if r.get("start_time")), default=None
            ),
        }
        _stats_cache[experiment_id] = result
        return result


@router.get("/experiments/{experiment_id}/runs")
async def list_runs(
    experiment_id: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
):
    try:
        raw = await _client().search_runs(
            experiment_ids=[experiment_id], max_results=max_results
        )
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # H-1: drop runs the caller does not own (admin sees all).
    visible_raw = [r for r in raw if _user_can_see_run_dict(user, r)]
    run_ids: list[str] = []
    for r in visible_raw:
        info = r.get("info") or {}
        rid = info.get("run_id") or info.get("run_uuid")
        if isinstance(rid, str) and rid:
            run_ids.append(rid)
    lolday_meta = await _fetch_lolday_job_meta(run_ids, session)
    return [_flatten_run(r, lolday_job_meta=lolday_meta) for r in visible_raw]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
):
    try:
        raw = await _client().get_run(run_id)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    # H-1: 404 (not 403) for non-owners to avoid leaking run existence.
    if not _user_can_see_run_dict(user, raw):
        raise HTTPException(status_code=404, detail="run not found")
    # #166: admin reading another user's MLflow run is an audit-worthy
    # cross-user data access. Skip when admin is reading their own data
    # (would otherwise drown the audit log in a high-volume noise stream).
    await _audit_cross_user_mlflow_read(session, user, raw, run_id)
    lolday_meta = await _fetch_lolday_job_meta([run_id], session)
    return _flatten_run(raw, lolday_job_meta=lolday_meta)


async def _audit_cross_user_mlflow_read(
    session: AsyncSession, user: User, raw_run: dict, run_id: str
) -> None:
    """Audit ``mlflow.run.read.cross_user`` when an admin reads another user's run.

    #166. Self-reads (admin reading their own data) are excluded -- the
    expected volume is high (every refresh of the admin's own dashboard)
    and the forensic value is zero.
    """
    if user.role != Role.ADMIN:
        return
    data = raw_run.get("data") or {}
    tags_list = data.get("tags") or []
    tags = {t["key"]: t["value"] for t in tags_list if "key" in t}
    owner_id_str = tags.get("lolday.user_id")
    if owner_id_str is None or owner_id_str == str(user.id):
        return
    from uuid import UUID as _UUID

    from app.services.audit import write_audit_log

    try:
        owner_uuid = _UUID(owner_id_str)
    except ValueError:
        return  # Malformed tag -- don't poison the audit log.
    await write_audit_log(
        session,
        actor_id=user.id,
        action="mlflow.run.read.cross_user",
        target_type="mlflow_run",
        target_id=owner_uuid,
        before=None,
        after={"run_id": run_id, "owner_id": owner_id_str},
    )
    await session.commit()


@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(
    run_id: str,
    user: Annotated[User, Depends(current_active_user)],
    path: str | None = None,
):
    # H-1: authorise via get_run before doing any artifact listing.
    try:
        run = await _client().get_run(run_id)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not _user_can_see_run_dict(user, run):
        raise HTTPException(status_code=404, detail="run not found")
    # H-2: block traversal / absolute paths on the optional ``path`` param.
    if path is not None:
        _validate_artifact_path(path)
    url = f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow/artifacts/list"
    params = {"run_id": run_id}
    if path:
        params["path"] = path
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        r = await c.get(url, params=params)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=r.text)
    return r.json()


@router.get("/runs/{run_id}/artifacts/download")
async def download_artifact(
    run_id: str,
    path: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> StreamingResponse:
    try:
        run = await _client().get_run(run_id)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    # H-1: ACL on owner.
    if not _user_can_see_run_dict(user, run):
        raise HTTPException(status_code=404, detail="run not found")
    # #166: same cross-user audit hook as get_run -- a download is the more
    # serious read (raw artifact bytes), at minimum needs the same trail.
    await _audit_cross_user_mlflow_read(session, user, run, run_id)
    # H-2: block traversal / absolute paths before interpolating ``path``.
    _validate_artifact_path(path)
    artifact_uri: str = run["info"]["artifact_uri"]
    prefix = "mlflow-artifacts:/"
    if not artifact_uri.startswith(prefix):
        raise HTTPException(
            status_code=502,
            detail=f"unexpected artifact_uri scheme: {artifact_uri!r}",
        )
    relative = artifact_uri[len(prefix) :].rstrip("/")
    # Percent-encode each segment defensively -- ``..`` is already rejected
    # by ``_validate_artifact_path``, but unencoded ``#`` / ``?`` / ``%``
    # would otherwise truncate the upstream URL or get re-interpreted.
    safe_path = "/".join(quote(p, safe="") for p in PurePosixPath(path).parts)
    url = (
        f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow-artifacts/artifacts/"
        f"{relative}/{safe_path}"
    )

    # RFC 6266: tell the browser to save with the artifact basename instead of
    # the URL's literal "download" segment. See spec §5.2 / plan Task 2.3.
    filename = PurePosixPath(path).name or "artifact"
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    # M-mlflow-stream: stream rather than buffer. Status check happens before
    # the StreamingResponse is constructed -- raising HTTPException AFTER the
    # response has started would silently degrade to a 200 with an empty body
    # because Starlette commits the response status when iteration begins.
    # AsyncExitStack centralises cleanup: the semaphore + AsyncClient + stream
    # context all unwind on success, on setup-error, on non-2xx, and on mid-
    # stream cancel (GeneratorExit reaches the generator's finally). Each
    # context manager is registered exactly once; stack.aclose() drains them
    # in reverse order with no risk of leaking a permit on a cleanup-path
    # exception (which the manual __aexit__ chain could not guarantee).
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await stack.enter_async_context(_MLFLOW_STREAM_SEM)
        client = await stack.enter_async_context(
            httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS)
        )
        upstream = await stack.enter_async_context(client.stream("GET", url))

        if upstream.status_code != 200:
            body = await upstream.aread()
            await stack.aclose()
            raise HTTPException(status_code=502, detail=body.decode("utf-8", "replace"))
    except BaseException:
        # Any failure between stack-open and the generator return must unwind
        # everything that was already entered. AsyncExitStack.aclose() is
        # idempotent, so the 502-path explicit close above does not double-
        # close here.
        await stack.aclose()
        raise

    async def _iter_upstream():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await stack.aclose()

    return StreamingResponse(
        _iter_upstream(),
        media_type=media_type,
        headers={"Content-Disposition": build_content_disposition(filename)},
    )
