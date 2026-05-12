import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
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
    internal,
    jobs,
    models_registry,
)

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

    reconciler_task: asyncio.Task | None = None
    if settings.RECONCILER_ENABLED:
        stop_event = asyncio.Event()
        reconciler_task = asyncio.create_task(reconciler_loop(stop_event))

    # Phase 6d: FIFO scheduler — submits queued_backend jobs to Volcano in
    # strict (priority DESC, submitted_at ASC) order.  Runs independently of
    # RECONCILER_ENABLED (which guards the vcjob ↔ DB sync loop); both can be
    # disabled together in tests via RECONCILER_ENABLED=false + not starting
    # this task.  Controlled by FIFO_RECONCILER_ENABLED (default True) so ops
    # can disable just this scheduler without touching the existing reconciler.
    fifo_task: asyncio.Task | None = None
    if settings.FIFO_RECONCILER_ENABLED:
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
        from app.services import gpu_signal, mlflow_client

        gpu_signal.close_http_client()
        await mlflow_client.close_http_client()


app = FastAPI(
    title="Lolday",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DOCS_ENABLED else None,
    redoc_url="/redoc" if settings.DOCS_ENABLED else None,
)

from app.middleware.body_size import BodySizeLimitMiddleware

app.add_middleware(BodySizeLimitMiddleware)

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

# Internal routes (build callbacks)
app.include_router(
    internal.router,
    prefix="/api/v1/internal",
    tags=["internal"],
)

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

# Cluster status routes (GPU allocation, Volcano queue depth)
app.include_router(
    cluster.router,
    prefix="/api/v1/cluster",
    tags=["cluster"],
)


@app.get("/api/v1/health", tags=["system"])
async def health():
    return {"status": "ok"}
