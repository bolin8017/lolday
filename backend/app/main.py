import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import settings
from app.db import async_session_maker, engine
from app.reconciler import reconciler_loop
from app.routers import (
    admin,
    builds,
    cluster,
    credentials,
    datasets,
    detectors,
    experiments_proxy,
    jobs,
    mlflow_authz,
    models_registry,
)
from app.services.mlflow_client import MlflowClient
from app.services.rate_limit import rate_limit_ip

logger = logging.getLogger(__name__)


async def _assert_schema_at_head() -> None:
    """Fail-fast if the DB's alembic revision doesn't match the code's.

    Running against an older schema would 500 on any query referencing a
    column the code assumes exists. Loud crash here halts the rollout so
    k8s keeps the previous replica serving traffic.
    """
    import pathlib

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError, ProgrammingError

    try:
        async with engine.begin() as conn:
            current = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    except (ProgrammingError, OperationalError):
        # Table doesn't exist — not an alembic-managed DB. Tests hit this
        # path (SQLite + conftest's create_all) and skip the check.
        return

    if current is None:
        return

    ini_path = pathlib.Path(__file__).resolve().parent.parent / "alembic.ini"
    if not ini_path.exists():
        # Image didn't ship migrations (unlikely after Phase 7.5 Dockerfile);
        # don't crash — just warn.
        logger.warning(
            "alembic.ini not found at %s — skipping schema head check", ini_path
        )
        return
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(ini_path.parent / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()

    if current != head:
        raise RuntimeError(
            f"DB schema mismatch: alembic_version={current!r}, code expects "
            f"head={head!r}. The `alembic-upgrade` pre-upgrade hook either "
            f"didn't run or rolled back. Investigate before rolling out."
        )


async def _bootstrap_dev_schema_if_empty() -> None:
    """Self-bootstrap the schema in dev mode when the DB is empty.

    Production deploys run migrations via the `alembic-upgrade` helm hook
    before the backend pod starts, so the DB is always at head. Test code
    runs `Base.metadata.create_all` in `conftest.py`. The remaining case is
    the E2E live-stack fixture (frontend-slow workflow): playwright spawns
    uvicorn against a fresh `sqlite+aiosqlite:///file::memory:` URL with no
    migration step, so the reconciler's first tick crashes with
    `no such table: detector_build`. Settings.validate_sso_config rejects
    AUTH_DEV_MODE=true in ENVIRONMENT=production so this branch can never
    fire there.
    """
    if not settings.AUTH_DEV_MODE:
        return

    from sqlalchemy import inspect

    from app.models import Base

    def _has_any_tables(sync_conn) -> bool:
        return bool(inspect(sync_conn).get_table_names())

    async with engine.begin() as conn:
        if await conn.run_sync(_has_any_tables):
            return
        await conn.run_sync(Base.metadata.create_all)
    logger.info("AUTH_DEV_MODE=true and DB empty — bootstrapped schema via create_all")


async def _run_fifo_reconciler_forever(period_s: int) -> None:
    """Periodically invoke reconcile_fifo_queue until cancelled.

    Pattern mirrors reconciler_loop: open a fresh session each tick,
    swallow per-tick errors so the loop never exits on transient failures.
    Cancellation via asyncio.CancelledError propagates through the sleep so
    the pod shuts down cleanly.
    """
    from app.reconciler.fifo_scheduler import reconcile_fifo_queue
    from app.services.k8s import core_v1

    logger.info("FIFO scheduler started (period=%ds)", period_s)
    while True:
        await asyncio.sleep(period_s)
        try:
            async with async_session_maker() as session:
                await reconcile_fifo_queue(session, core_v1())
        except asyncio.CancelledError:
            raise
        except Exception:
            from app.metrics import BACKEND_ERRORS

            BACKEND_ERRORS.labels(stage="fifo_scheduler_iteration").inc()
            logger.exception("fifo_scheduler tick failed")
    # Unreachable; loop exits only via CancelledError.


def _install_spec_lane_stubs(app: FastAPI) -> None:
    """Replace ``app.services.k8s.{batch_v1,core_v1,volcano_v1alpha1}`` and
    ``app.state.mlflow`` with in-process stubs.

    Gated on ``settings.SPEC_LANE_STUBS=true``. Idempotent — re-binding a
    module attribute that already points to a stub is a no-op. Singletons
    live on ``app.state`` so the reconciler / FIFO scheduler / route
    handlers share state, mirroring a real K8s API server.

    Production refuses boot when the flag is true (see
    ``Settings.validate_sso_config``). The leading-underscore module
    name (``app.services._stubs``) marks the consumers as internal.
    """
    import importlib

    from app.services import _stubs
    from app.services import k8s as _k8s

    _k8s.load_config = _stubs.safe_load_config  # type: ignore[assignment]  # SPEC_LANE_STUBS path; matches load_config signature

    batch = _stubs.StubBatch()
    core = _stubs.StubCore()
    volcano = _stubs.StubVolcano()
    app.state.stub_batch = batch
    app.state.stub_core = core
    app.state.stub_volcano = volcano

    _k8s.batch_v1 = lambda: batch
    _k8s.core_v1 = lambda: core
    _k8s.volcano_v1alpha1 = lambda: volcano

    name_to_singleton = {
        "batch_v1": batch,
        "core_v1": core,
        "volcano_v1alpha1": volcano,
    }
    for module_path, name in _stubs.CALLER_MODULE_REBIND_TARGETS:
        module = importlib.import_module(module_path)
        target = name_to_singleton[name]
        setattr(module, name, (lambda t=target: t))

    # `app.services.git.list_remote_tags` hits the public GitHub REST API.
    # The trigger-build E2E flow needs a deterministic, network-free response
    # so the Select inside the build dialog renders at least one tag and the
    # Build button enables. Rebind both the canonical module and the routers
    # that imported the symbol via `from app.services.git import ...`.
    from app.services import git as _git

    _git.list_remote_tags = (
        _stubs.stub_list_remote_tags
    )  # SPEC_LANE_STUBS path; matches signature
    for module_path in ("app.routers.detectors",):
        module = importlib.import_module(module_path)
        if hasattr(module, "list_remote_tags"):
            module.list_remote_tags = _stubs.stub_list_remote_tags  # type: ignore[attr-defined]

    # `_get_user_pat` is module-private in `routers/detectors.py`. Real
    # PAT lookup decrypts a `UserGitCredential` row with `TokenCipher`,
    # which requires FERNET_KEYS — not set in the Playwright env. Stub
    # to a non-None placeholder so the `create_build` `credential_missing`
    # 400 guard clears; the K8s side of the build call is itself stubbed.
    from app.routers import detectors as _detectors

    _detectors._get_user_pat = _stubs.stub_get_user_pat  # SPEC_LANE_STUBS path

    app.state.mlflow = _stubs.StubMlflowClient()
    logger.info("SPEC_LANE_STUBS=true — installed in-process K8s + MLflow stubs")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 7.4: flag misconfigured deploy before a user waits weeks for
    # notifications that never arrive. No metric — "disabled" is a config
    # state, not an error; a startup log is the right level.
    if not settings.DISCORD_WEBHOOK_URL_EVENTS:
        logger.warning(
            "DISCORD_WEBHOOK_URL_EVENTS is empty — user-event Discord notifications "
            "are disabled. Set the secret `discord-events/webhook-url` to enable."
        )
    # Phase 7.5: schema is managed by Alembic via the `alembic-upgrade` helm
    # pre-upgrade hook Job (templates/alembic-upgrade-hook.yaml). The previous
    # `Base.metadata.create_all` here couldn't ALTER existing tables and
    # silently masked schema drift on column additions. Verify the hook
    # actually ran to head — otherwise this pod would 500 on queries that
    # reference new columns, and k8s readiness would pass until traffic hits.
    # Skip gracefully when alembic_version is absent (tests: SQLite create_all;
    # fresh install before stamp).
    await _assert_schema_at_head()
    # E2E live-stack (frontend-slow workflow) spawns uvicorn against a fresh
    # sqlite+aiosqlite in-memory DB with no Alembic step — bootstrap the
    # schema so the reconciler does not crash on its first tick. Gated on
    # AUTH_DEV_MODE so this never runs in production.
    await _bootstrap_dev_schema_if_empty()
    # Admin bootstrap happens in the phase10_sso_admin_email migration
    # (renames the seed admin@lolday.dev row to the operator's SSO email);
    # subsequent admins are promoted via `PATCH /admin/users/{id}`.

    # Harbor post-install init: idempotent, safe to retry on every startup
    try:
        from app.services.harbor_init import init_harbor

        await init_harbor()
    except Exception:
        from app.metrics import BACKEND_ERRORS

        BACKEND_ERRORS.labels(stage="harbor_init").inc()
        logger.exception("harbor init failed — continuing, build pipeline may not work")

    # app.state-managed httpx.AsyncClient + MlflowClient — created before the
    # reconciler task so the task receives the live client instance.
    # The legacy module-level _HTTP_CLIENT shim is removed in T13 (this step).
    if settings.SPEC_LANE_STUBS:
        # Stubs replace the real K8s + MLflow clients in-process. Used by
        # the frontend-slow Playwright live-stack to avoid leaking Volcano
        # CRs and to make CI work without a kubeconfig. See spec
        # 2026-05-17-frontend-slow-stub-layer-design.md.
        _install_spec_lane_stubs(app)
    else:
        app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        app.state.mlflow = MlflowClient.from_settings(settings, app.state.http)

    # Skip both background reconcilers when running in the spec-lane stubs
    # lifespan. They have nothing meaningful to do against the in-memory
    # `StubVolcano` / `StubMlflowClient` (no real CRs to sync, no real
    # cluster GPU state to check), and their per-tick DB reads/writes
    # collide with HTTP request writes on the shared-cache aiosqlite
    # backend — every multi-write spec (e.g.
    # `tests/e2e/models/transfer-and-delete.spec.ts`) flapped on
    # `OperationalError: database is locked`. The `busy_timeout` PRAGMA in
    # `app/db.py` softens transient contention; dropping the perpetual
    # reconciler loop eliminates the structural source of contention.
    reconciler_task: asyncio.Task | None = None
    if settings.RECONCILER_ENABLED and not settings.SPEC_LANE_STUBS:
        stop_event = asyncio.Event()
        reconciler_task = asyncio.create_task(
            reconciler_loop(stop_event, app.state.mlflow)
        )

    # Phase 6d: FIFO scheduler — submits queued_backend jobs to Volcano in
    # strict (priority DESC, submitted_at ASC) order.  Runs independently of
    # RECONCILER_ENABLED (which guards the vcjob ↔ DB sync loop); both can be
    # disabled together in tests via RECONCILER_ENABLED=false + not starting
    # this task.  Controlled by FIFO_RECONCILER_ENABLED (default True) so ops
    # can disable just this scheduler without touching the existing reconciler.
    # Same `SPEC_LANE_STUBS` skip applies — see comment above.
    fifo_task: asyncio.Task | None = None
    if settings.FIFO_RECONCILER_ENABLED and not settings.SPEC_LANE_STUBS:
        fifo_task = asyncio.create_task(
            _run_fifo_reconciler_forever(settings.FIFO_RECONCILER_PERIOD_SECONDS)
        )

    yield

    # Best-effort hygiene cleanup: shut the shared Prometheus httpx.Client
    # even if the task awaits above raise.  The close itself is independent
    # of those tasks and the OS reaps sockets either way; the `finally` only
    # exists so a stuck task does not silently skip the ResourceWarning
    # suppression we want at shutdown.  Spec
    # docs/superpowers/specs/2026-05-12-backend-httpx-client-leak-fix-design.md
    # §5.2.
    try:
        if reconciler_task is not None:
            stop_event.set()
            await reconciler_task

        if fifo_task is not None:
            fifo_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await fifo_task
    finally:
        from app.services import gpu_signal

        gpu_signal.close_http_client()
        # SPEC_LANE_STUBS skips app.state.http construction (the StubMlflowClient
        # doesn't need it). Guard the aclose() so teardown doesn't AttributeError.
        if not settings.SPEC_LANE_STUBS:
            await app.state.http.aclose()


app = FastAPI(
    title="Lolday",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DOCS_ENABLED else None,
    redoc_url="/redoc" if settings.DOCS_ENABLED else None,
    # #165: /docs and /redoc were gated but /openapi.json was always served.
    # The Swagger UI loads its schema from /openapi.json, so this silently
    # leaks the API surface even with DOCS_ENABLED=false. Gate the schema
    # endpoint on the same flag.
    openapi_url="/openapi.json" if settings.DOCS_ENABLED else None,
    # Document framework-level responses every authenticated route can
    # emit so the OpenAPI contract is honest. 400 comes from FastAPI's
    # own body-parser (fastapi/routing.py: `HTTPException(400, "There
    # was an error parsing the body")` for malformed JSON); 401/403 from
    # the cf_access auth dependency / role guards; 404 from any handler
    # that does `session.get(Model, id) → raise 404`; 500 covers any
    # unexpected server-side crash. Schemathesis 4's broader generation
    # exposes paths that schemathesis 3 never reached.
    responses={
        400: {"description": "Malformed request body or bad input"},
        401: {"description": "Authentication required"},
        403: {"description": "Forbidden"},
        404: {"description": "Not found"},
        500: {"description": "Internal server error"},
    },
)

from app.middleware.body_size import BodySizeLimitMiddleware
from app.middleware.csrf import CSRFOriginMiddleware

app.add_middleware(BodySizeLimitMiddleware)
# M-csrf: gate state-changing methods on Origin / Sec-Fetch-Site. See
# backend/app/middleware/csrf.py and plan section D1.
app.add_middleware(CSRFOriginMiddleware)

# Intentionally not wrapped in try/except: if metrics wiring fails the pod
# should CrashLoopBackOff so LoldayCoreServiceDown fires — silently losing
# scrape targets is worse than a loud restart.
Instrumentator().instrument(app).expose(
    app,
    endpoint="/metrics",
    include_in_schema=False,
)

# Primary auth is Cloudflare Access SSO (see app/auth/cf_access.py).
# Per-user rate limits live in app/services/rate_limit.py.

# User routes — /me served by our cf_access_user-backed router.
from app.routers import users_me

app.include_router(
    users_me.router,
    prefix="/api/v1/users",
    tags=["users"],
)

# Admin routes
app.include_router(
    admin.router,
    prefix="/api/v1/admin",
    tags=["admin"],
)

# Credentials routes
app.include_router(
    credentials.router,
    prefix="/api/v1/users",
    tags=["credentials"],
)

# Datasets routes
app.include_router(
    datasets.router,
    prefix="/api/v1/datasets",
    tags=["datasets"],
)

# Detectors routes
app.include_router(
    detectors.router,
    prefix="/api/v1/detectors",
    tags=["detectors"],
)

# Flat builds alias — /api/v1/builds/<id>
app.include_router(
    builds.router,
    prefix="/api/v1/builds",
    tags=["builds"],
)

# Jobs routes
app.include_router(
    jobs.router,
    prefix="/api/v1/jobs",
    tags=["jobs"],
)

# Internal routes have moved to internal_app (port 8001) — see app/internal_app.py.
# /api/v1/internal/* is no longer served on the public port 8000.

# Model Registry routes
app.include_router(
    models_registry.router,
    prefix="/api/v1/models",
    tags=["models"],
)

# MLflow proxy routes
app.include_router(
    experiments_proxy.router,
    prefix="/api/v1",
    tags=["mlflow"],
)

# H-15: Traefik ForwardAuth target for /mlflow/* per-user ACL.
# Mounted on the PUBLIC app (port 8000) — Traefik in kube-system reaches
# this endpoint via the same Service the browser hits.  Per-call auth is
# CF Access JWT (browser) or Job bearer token (job pod); MLflow Service
# itself is locked down by the T9 (H-12) NetworkPolicy to backend +
# Traefik so this is the only path that can reach MLflow.
app.include_router(
    mlflow_authz.router,
    prefix="/api/v1/mlflow-authz",
    tags=["mlflow-authz"],
)

# Cluster status routes (GPU allocation, Volcano queue depth)
app.include_router(
    cluster.router,
    prefix="/api/v1/cluster",
    tags=["cluster"],
)

# D3.3 — dev-mode E2E seed endpoint. The router registers unconditionally;
# the handler gates on settings.AUTH_DEV_MODE and returns 404 when off, so
# production never exposes the surface (defence in depth on top of the
# existing Settings.validate_sso_config rejection of AUTH_DEV_MODE=true in
# ENVIRONMENT=production boots).
from app.routers import dev_seed  # router registration order

app.include_router(dev_seed.router)


# H-26: IP-keyed rate limit. 120/60s = 2 RPS per source — well above any
# legitimate probe cadence (Cloudflare Access health check is 30s, browser
# status ping is 60s, kubelet now targets /livez on :8001 instead). A 1000
# RPS DoS attacker is converted to 2 RPS per IP + 429 for the rest, and
# lolday_rate_limit_hits_total{prefix="health"} feeds LoldayRateLimitSpike
# (P5). kubelet liveness is retargeted at /livez on :8001 in the chart so
# this 429 does NOT cause pod restarts.
@app.get(
    "/api/v1/health",
    tags=["system"],
    dependencies=[Depends(rate_limit_ip("health", 120, 60))],
)
async def health():
    return {"status": "ok"}
