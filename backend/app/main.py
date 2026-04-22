import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func, select
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import settings
from app.db import async_session_maker, engine
from app.models import Role, User
from app.reconciler import reconciler_loop
from app.routers import admin, builds, cluster, credentials, datasets, detectors, experiments_proxy, internal, jobs, models_registry

logger = logging.getLogger(__name__)


async def _assert_schema_at_head() -> None:
    """Fail-fast if the DB's alembic revision doesn't match the code's.

    Running against an older schema would 500 on any query referencing a
    column the code assumes exists. Loud crash here halts the rollout so
    k8s keeps the previous replica serving traffic.
    """
    import pathlib
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError, ProgrammingError
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    try:
        async with engine.begin() as conn:
            current = (await conn.execute(
                text("SELECT version_num FROM alembic_version")
            )).scalar_one_or_none()
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
        logger.warning("alembic.ini not found at %s — skipping schema head check", ini_path)
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
    # Phase 10.2: FIRST_ADMIN seed block removed — password auth is gone and
    # new admins are promoted via SSO + `PATCH /admin/users/{id}` (admin UI).
    # Bootstrap of the very first admin happens by running the
    # phase10_sso_admin_email Alembic migration with SSO_ADMIN_EMAIL set to
    # the operator, which renames the legacy admin@lolday.dev row in place.

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

    yield

    if reconciler_task is not None:
        stop_event.set()
        await reconciler_task


app = FastAPI(
    title="Lolday",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DOCS_ENABLED else None,
    redoc_url="/redoc" if settings.DOCS_ENABLED else None,
)

# Intentionally not wrapped in try/except: if metrics wiring fails the pod
# should CrashLoopBackOff so LoldayCoreServiceDown fires — silently losing
# scrape targets is worse than a loud restart.
Instrumentator().instrument(app).expose(
    app, endpoint="/metrics", include_in_schema=False,
)

# Phase 10.2: fastapi-users password/cookie auth routes removed entirely.
# Primary auth is now Cloudflare Access SSO (see app/auth/cf_access.py). The
# login-endpoint rate-limit middleware is gone with the routes it protected;
# per-user rate limits continue to live in app/services/rate_limit.py.

# User routes — /me served by our cf_access_user-backed router.
from app.routers import users_me  # noqa: E402
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
