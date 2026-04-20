import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, select

from app.config import settings
from app.db import async_session_maker, engine
from app.models import Base, Role, User
from app.reconciler import reconciler_loop
from app.routers import admin, credentials, datasets, detectors, experiments_proxy, internal, jobs, models_registry
from app.schemas import AdminUserUpdate, UserCreate, UserRead, UserUpdate
from app.users import auth_backend, cookie_auth_backend, fastapi_users, UserManager

logger = logging.getLogger(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if settings.FIRST_ADMIN_EMAIL and settings.FIRST_ADMIN_PASSWORD:
        async with async_session_maker() as session:
            result = await session.execute(
                select(func.count()).select_from(User)
            )
            if result.scalar() == 0:
                from fastapi_users.db import SQLAlchemyUserDatabase

                user_db = SQLAlchemyUserDatabase(session, User)
                user_manager = UserManager(user_db)
                user = await user_manager.create(
                    UserCreate(
                        email=settings.FIRST_ADMIN_EMAIL,
                        password=settings.FIRST_ADMIN_PASSWORD,
                        is_superuser=True,
                        is_verified=True,
                    )
                )
                user.role = Role.ADMIN
                session.add(user)
                await session.commit()
                logger.info("Seed admin created: %s", user.email)

    # Harbor post-install init: idempotent, safe to retry on every startup
    try:
        from app.services.harbor_init import init_harbor
        await init_harbor()
    except Exception:
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

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Auth routes
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/api/v1/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_auth_router(cookie_auth_backend),
    prefix="/api/v1/auth/cookie",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/api/v1/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_reset_password_router(),
    prefix="/api/v1/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_verify_router(UserRead),
    prefix="/api/v1/auth",
    tags=["auth"],
)

# User routes
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
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


@app.get("/api/v1/health", tags=["system"])
async def health():
    return {"status": "ok"}
