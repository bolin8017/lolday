# Phase 2: Backend Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a FastAPI backend with PostgreSQL, Redis, JWT auth (FastAPI Users), and RBAC to the existing K3s cluster.

**Architecture:** Modular FastAPI app using FastAPI Users for auth, async SQLAlchemy + asyncpg for PostgreSQL, Redis for rate limiting. Deployed as K8s workloads via the existing Helm umbrella chart.

**Tech Stack:** FastAPI, FastAPI Users, SQLAlchemy (async), asyncpg, Redis, slowapi, Alembic, uv, Helm

**Spec:** `docs/superpowers/specs/2026-04-13-phase2-backend-core-design.md`

**Server:** server30 (Ubuntu 24.04, K3s v1.34.6, 2× RTX 2080 Ti)

**Constraints:**

- `bolin8017` has no persistent sudo. Docker commands run as user (Docker group).
- CLI tools in `~/.local/bin/`.
- SSH (port 9453) must never be disrupted.

---

## File Structure

```
backend/
├── pyproject.toml
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── schemas.py
│   ├── users.py
│   ├── deps.py
│   └── routers/
│       ├── __init__.py
│       └── admin.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_auth.py
│   └── test_admin.py
└── Dockerfile

charts/lolday/templates/
├── (Phase 1 — existing)
├── postgresql.yaml          # NEW
├── redis.yaml               # NEW
└── backend.yaml             # NEW
```

---

### Task 1: Project Scaffolding

**Files:**

- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p backend/app/routers backend/tests backend/alembic/versions
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[project]
name = "lolday-backend"
version = "0.1.0"
description = "Lolday platform backend"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "fastapi-users[sqlalchemy]>=14.0.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "asyncpg>=0.30.0",
    "alembic>=1.15.0",
    "pydantic-settings>=2.8.0",
    "redis[hiredis]>=5.2.0",
    "slowapi>=0.1.9",
]

[tool.uv]
dev-dependencies = [
    "httpx>=0.28.0",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.25.0",
    "aiosqlite>=0.21.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: Create app/**init**.py and tests/**init**.py**

Both files are empty (make directories Python packages).

- [ ] **Step 4: Create app/config.py**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://lolday:password@postgresql:5432/lolday"
    REDIS_URL: str = "redis://redis:6379/0"
    JWT_SECRET: str = "CHANGE-ME-IN-PRODUCTION"
    JWT_LIFETIME_SECONDS: int = 3600
    FIRST_ADMIN_EMAIL: str = ""
    FIRST_ADMIN_PASSWORD: str = ""
    DOCS_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: str = "60/minute"
    RATE_LIMIT_AUTH: str = "10/minute"


settings = Settings()
```

- [ ] **Step 5: Install dependencies**

```bash
cd backend && uv sync
```

Expected: `.venv` created, all packages installed.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/__init__.py backend/app/config.py backend/tests/__init__.py
git commit -m "feat(backend): scaffold project with dependencies"
```

---

### Task 2: Database Layer & Models

**Files:**

- Create: `backend/app/db.py`
- Create: `backend/app/models.py`
- Create: `backend/app/schemas.py`

- [ ] **Step 1: Create app/db.py**

```python
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
```

- [ ] **Step 2: Create app/models.py**

```python
import enum
from datetime import datetime

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    USER = "user"


class User(SQLAlchemyBaseUserTableUUID, Base):
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, name="role_enum"), default=Role.USER, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

- [ ] **Step 3: Create app/schemas.py**

```python
import uuid
from datetime import datetime

from fastapi_users import schemas

from app.models import Role


class UserRead(schemas.BaseUser[uuid.UUID]):
    role: Role
    display_name: str | None = None
    created_at: datetime | None = None


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None


class UserUpdate(schemas.BaseUserUpdate):
    role: Role | None = None
    display_name: str | None = None
```

- [ ] **Step 4: Verify imports work**

```bash
cd backend && uv run python -c "from app.models import User, Role; from app.schemas import UserRead, UserCreate, UserUpdate; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/app/models.py backend/app/schemas.py
git commit -m "feat(backend): add database layer, User model, and schemas"
```

---

### Task 3: FastAPI Users Auth Setup

**Files:**

- Create: `backend/app/users.py`
- Create: `backend/app/deps.py`

- [ ] **Step 1: Create app/users.py**

```python
import uuid

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.models import User


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.JWT_SECRET
    verification_token_secret = settings.JWT_SECRET


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="api/v1/auth/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.JWT_SECRET,
        lifetime_seconds=settings.JWT_LIFETIME_SECONDS,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)

current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
```

- [ ] **Step 2: Create app/deps.py**

```python
from fastapi import Depends, HTTPException, status

from app.models import Role, User
from app.users import current_active_user

ROLE_HIERARCHY = {Role.USER: 0, Role.DEVELOPER: 1, Role.ADMIN: 2}


def require_role(min_role: Role):
    async def _check(user: User = Depends(current_active_user)):
        if ROLE_HIERARCHY[user.role] < ROLE_HIERARCHY[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _check
```

- [ ] **Step 3: Verify imports**

```bash
cd backend && uv run python -c "from app.users import fastapi_users, auth_backend; from app.deps import require_role; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/users.py backend/app/deps.py
git commit -m "feat(backend): configure FastAPI Users auth and RBAC deps"
```

---

### Task 4: FastAPI Application & Routers

**Files:**

- Create: `backend/app/main.py`
- Create: `backend/app/routers/__init__.py`
- Create: `backend/app/routers/admin.py`

- [ ] **Step 1: Create app/routers/**init**.py**

Empty file.

- [ ] **Step 2: Create app/routers/admin.py**

```python
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import require_role
from app.models import Role, User
from app.schemas import UserRead

router = APIRouter()


@router.get("/users", response_model=list[UserRead])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    _user: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(select(User).offset(skip).limit(limit))
    return result.scalars().all()
```

- [ ] **Step 3: Create app/main.py**

```python
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
from app.routers import admin
from app.schemas import UserCreate, UserRead, UserUpdate
from app.users import auth_backend, fastapi_users, get_user_db, UserManager

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
    yield


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


@app.get("/api/v1/health", tags=["system"])
async def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py backend/app/routers/__init__.py backend/app/routers/admin.py
git commit -m "feat(backend): add FastAPI app with auth routes, admin, and health"
```

---

### Task 5: Tests

**Files:**

- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_auth.py`
- Create: `backend/tests/test_admin.py`

- [ ] **Step 1: Create tests/conftest.py**

```python
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_async_session
from app.models import Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"
test_engine = create_async_engine(TEST_DATABASE_URL)
test_session_maker = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    from app.main import app

    async def override():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def register_user(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    return resp.json()


async def login_user(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    return resp.json()["access_token"]


async def auth_header(client: AsyncClient, email: str, password: str) -> dict:
    token = await login_user(client, email, password)
    return {"Authorization": f"Bearer {token}"}
```

- [ ] **Step 2: Create tests/test_auth.py**

```python
import pytest
from httpx import AsyncClient

from tests.conftest import auth_header, register_user


@pytest.mark.asyncio
async def test_register(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "Str0ngP@ss!"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "test@example.com"
    assert data["role"] == "user"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    await register_user(client, "dup@example.com", "Str0ngP@ss!")
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "dup@example.com", "password": "Str0ngP@ss!"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_login(client: AsyncClient):
    await register_user(client, "login@example.com", "Str0ngP@ss!")
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "login@example.com", "password": "Str0ngP@ss!"},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    await register_user(client, "wrong@example.com", "Str0ngP@ss!")
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "wrong@example.com", "password": "bad"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_profile(client: AsyncClient):
    await register_user(client, "me@example.com", "Str0ngP@ss!")
    headers = await auth_header(client, "me@example.com", "Str0ngP@ss!")
    resp = await client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


@pytest.mark.asyncio
async def test_get_profile_unauthenticated(client: AsyncClient):
    resp = await client.get("/api/v1/users/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 3: Create tests/test_admin.py**

```python
import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Role, User
from tests.conftest import auth_header, register_user

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"
test_engine = create_async_engine(TEST_DATABASE_URL)
test_session_maker = async_sessionmaker(test_engine, expire_on_commit=False)


async def make_admin(email: str):
    async with test_session_maker() as session:
        await session.execute(
            update(User).where(User.email == email).values(role=Role.ADMIN)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_admin_list_users(client: AsyncClient):
    await register_user(client, "admin@example.com", "Str0ngP@ss!")
    await make_admin("admin@example.com")
    await register_user(client, "user1@example.com", "Str0ngP@ss!")
    headers = await auth_header(client, "admin@example.com", "Str0ngP@ss!")
    resp = await client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_non_admin_cannot_list_users(client: AsyncClient):
    await register_user(client, "regular@example.com", "Str0ngP@ss!")
    headers = await auth_header(client, "regular@example.com", "Str0ngP@ss!")
    resp = await client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_users(client: AsyncClient):
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 401
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/ -v
```

Expected: All tests pass. The lifespan `create_all` + test fixtures handle table creation. slowapi rate limiter may log a Redis connection warning (no Redis in test), but tests should pass because rate limiting is not enforced in test mode.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/
git commit -m "test(backend): add auth and admin endpoint tests"
```

---

### Task 6: Alembic Migrations

**Files:**

- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`

- [ ] **Step 1: Initialize Alembic**

```bash
cd backend && uv run alembic init alembic
```

This creates `alembic.ini` and `alembic/env.py`. We will overwrite `env.py` with async support.

- [ ] **Step 2: Update alembic.ini**

Set `sqlalchemy.url` to empty (we load from config):

Change line:

```
sqlalchemy.url = driver://user:pass@localhost/dbname
```

to:

```
sqlalchemy.url =
```

- [ ] **Step 3: Overwrite alembic/env.py for async**

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(settings.DATABASE_URL)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Generate initial migration**

Requires a running PostgreSQL. Skip generation for now — the migration will be generated after PostgreSQL is deployed in Task 8. The lifespan `create_all` handles table creation in dev.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic.ini backend/alembic/
git commit -m "feat(backend): add Alembic async migration setup"
```

---

### Task 7: Dockerfile

**Files:**

- Create: `backend/Dockerfile`

- [ ] **Step 1: Create Dockerfile**

```dockerfile
FROM python:3.12-slim AS base

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY app/ ./app/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Build image**

```bash
cd backend && docker build -t lolday-backend:latest .
```

Expected: Build succeeds.

- [ ] **Step 3: Verify image runs (smoke test)**

```bash
docker run --rm -e DATABASE_URL=sqlite+aiosqlite:///./test.db -e REDIS_URL=redis://localhost:6379/0 -p 8000:8000 lolday-backend:latest &
sleep 3
curl -s http://localhost:8000/api/v1/health
docker stop $(docker ps -q --filter ancestor=lolday-backend:latest)
```

Expected: `{"status":"ok"}`. The app may log Redis connection warnings (no Redis), but health endpoint works.

- [ ] **Step 4: Commit**

```bash
git add backend/Dockerfile
git commit -m "feat(backend): add Dockerfile"
```

---

### Task 8: Helm Chart Updates

**Files:**

- Create: `charts/lolday/templates/postgresql.yaml`
- Create: `charts/lolday/templates/redis.yaml`
- Create: `charts/lolday/templates/backend.yaml`
- Modify: `charts/lolday/values.yaml`

- [ ] **Step 1: Create templates/postgresql.yaml**

```yaml
{{- if .Values.postgresql.enabled }}
apiVersion: v1
kind: Secret
metadata:
  name: postgresql
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: postgresql
    {{- include "lolday.labels" . | nindent 4 }}
type: Opaque
stringData:
  POSTGRES_USER: {{ .Values.postgresql.auth.username | quote }}
  POSTGRES_PASSWORD: {{ .Values.postgresql.auth.password | quote }}
  POSTGRES_DB: {{ .Values.postgresql.auth.database | quote }}
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgresql
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: postgresql
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  serviceName: postgresql
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/component: postgresql
  template:
    metadata:
      labels:
        app.kubernetes.io/component: postgresql
    spec:
      containers:
        - name: postgresql
          image: postgres:16
          ports:
            - containerPort: 5432
          env:
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          envFrom:
            - secretRef:
                name: postgresql
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          livenessProbe:
            exec:
              command: ["pg_isready", "-U", "lolday"]
            initialDelaySeconds: 30
            periodSeconds: 10
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "lolday"]
            initialDelaySeconds: 5
            periodSeconds: 5
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: [ReadWriteOnce]
        resources:
          requests:
            storage: {{ .Values.postgresql.storage.size }}
---
apiVersion: v1
kind: Service
metadata:
  name: postgresql
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: postgresql
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  type: ClusterIP
  ports:
    - port: 5432
      targetPort: 5432
  selector:
    app.kubernetes.io/component: postgresql
{{- end }}
```

- [ ] **Step 2: Create templates/redis.yaml**

```yaml
{{- if .Values.redis.enabled }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: redis
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/component: redis
  template:
    metadata:
      labels:
        app.kubernetes.io/component: redis
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          ports:
            - containerPort: 6379
          command: ["redis-server", "--maxmemory", "128mb", "--maxmemory-policy", "allkeys-lru"]
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 192Mi
          livenessProbe:
            exec:
              command: ["redis-cli", "ping"]
            initialDelaySeconds: 5
            periodSeconds: 10
          readinessProbe:
            exec:
              command: ["redis-cli", "ping"]
            initialDelaySeconds: 3
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: redis
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: redis
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  type: ClusterIP
  ports:
    - port: 6379
      targetPort: 6379
  selector:
    app.kubernetes.io/component: redis
{{- end }}
```

- [ ] **Step 3: Create templates/backend.yaml**

```yaml
{{- if .Values.backend.enabled }}
apiVersion: v1
kind: Secret
metadata:
  name: backend
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: backend
    {{- include "lolday.labels" . | nindent 4 }}
type: Opaque
stringData:
  JWT_SECRET: {{ .Values.backend.jwtSecret | quote }}
  DATABASE_URL: "postgresql+asyncpg://{{ .Values.postgresql.auth.username }}:{{ .Values.postgresql.auth.password }}@postgresql:5432/{{ .Values.postgresql.auth.database }}"
  REDIS_URL: "redis://redis:6379/0"
  FIRST_ADMIN_EMAIL: {{ .Values.backend.firstAdmin.email | quote }}
  FIRST_ADMIN_PASSWORD: {{ .Values.backend.firstAdmin.password | quote }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: backend
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.backend.replicas }}
  selector:
    matchLabels:
      app.kubernetes.io/component: backend
  template:
    metadata:
      labels:
        app.kubernetes.io/component: backend
    spec:
      containers:
        - name: backend
          image: {{ .Values.backend.image }}
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef:
                name: backend
          env:
            - name: DOCS_ENABLED
              value: {{ .Values.backend.env.DOCS_ENABLED | quote }}
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
          livenessProbe:
            httpGet:
              path: /api/v1/health
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /api/v1/health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: backend
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: backend
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  type: ClusterIP
  ports:
    - port: 8000
      targetPort: 8000
  selector:
    app.kubernetes.io/component: backend
{{- end }}
```

- [ ] **Step 4: Update values.yaml**

Add to existing `charts/lolday/values.yaml`:

```yaml
# =============================================================================
# Backend API
# =============================================================================
backend:
  enabled: true
  image: registry.lolday.svc.cluster.local:5000/lolday-backend:latest
  replicas: 1
  jwtSecret: "" # --set at deploy time, NEVER commit
  firstAdmin:
    email: "" # --set at deploy time
    password: "" # --set at deploy time
  env:
    DOCS_ENABLED: "true"

# =============================================================================
# PostgreSQL
# =============================================================================
postgresql:
  enabled: true
  storage:
    size: 10Gi
  auth:
    database: lolday
    username: lolday
    password: "" # --set at deploy time, NEVER commit

# =============================================================================
# Redis
# =============================================================================
redis:
  enabled: true
```

- [ ] **Step 5: Lint Helm chart**

```bash
helm lint charts/lolday
```

Expected: `0 chart(s) failed`

- [ ] **Step 6: Commit**

```bash
git add charts/lolday/templates/postgresql.yaml charts/lolday/templates/redis.yaml charts/lolday/templates/backend.yaml charts/lolday/values.yaml
git commit -m "feat(helm): add PostgreSQL, Redis, and backend templates"
```

---

### Task 9: Build, Push, Deploy & E2E Verification

**Files:** None (operations only)

**Prerequisites:** Tasks 1–8 complete, K3s running, registry pod running

- [ ] **Step 1: Tag and push backend image to in-cluster registry**

```bash
# Port-forward the registry
kubectl -n lolday port-forward svc/registry 5000:5000 &
sleep 2

# Build
cd backend && docker build -t localhost:5000/lolday-backend:latest .

# Push
docker push localhost:5000/lolday-backend:latest

# Stop port-forward
kill %1
```

- [ ] **Step 2: Update deploy.sh for Phase 2 secrets**

Add to `scripts/deploy.sh` the new `--set` flags. Replace the `helm upgrade` line:

```bash
helm upgrade --install lolday "$CHART_DIR" \
  -n lolday --create-namespace \
  --set cloudflare.enabled="${CF_ENABLED:-false}" \
  --set cloudflare.tunnelToken="${CF_TUNNEL_TOKEN:-}" \
  --set postgresql.auth.password="${PG_PASSWORD:-lolday-dev-password}" \
  --set backend.jwtSecret="${JWT_SECRET:-lolday-dev-jwt-secret}" \
  --set backend.firstAdmin.email="${ADMIN_EMAIL:-admin@lolday.local}" \
  --set backend.firstAdmin.password="${ADMIN_PASSWORD:-Admin123!}" \
  --wait --timeout 5m
```

- [ ] **Step 3: Deploy**

```bash
PG_PASSWORD=lolday-dev-password \
JWT_SECRET=lolday-dev-jwt-secret \
ADMIN_EMAIL=admin@lolday.local \
ADMIN_PASSWORD=Admin123! \
bash scripts/deploy.sh
```

Expected: PostgreSQL, Redis, backend pods all Running.

- [ ] **Step 4: Verify all pods**

```bash
kubectl -n lolday get pods
```

Expected:

```
NAME                        READY   STATUS    RESTARTS   AGE
backend-xxx                 1/1     Running   0          ...
postgresql-0                1/1     Running   0          ...
redis-xxx                   1/1     Running   0          ...
registry-xxx                1/1     Running   0          ...
```

- [ ] **Step 5: Verify health endpoint**

```bash
kubectl -n lolday port-forward svc/backend 8000:8000 &
sleep 2
curl -s http://localhost:8000/api/v1/health
kill %1
```

Expected: `{"status":"ok"}`

- [ ] **Step 6: E2E test — register and login**

```bash
kubectl -n lolday port-forward svc/backend 8000:8000 &
sleep 2

# Register a new user
curl -s -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"TestP@ss123"}'

# Login
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@example.com&password=TestP@ss123"

# Login as seed admin
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin@lolday.local&password=Admin123!"

kill %1
```

Expected: Registration returns 201 with user JSON. Both logins return 200 with `access_token`.

- [ ] **Step 7: E2E test — admin list users**

```bash
kubectl -n lolday port-forward svc/backend 8000:8000 &
sleep 2

# Get admin token
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin@lolday.local&password=Admin123!" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# List users as admin
curl -s http://localhost:8000/api/v1/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Try as regular user (should fail)
USER_TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@example.com&password=TestP@ss123" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:8000/api/v1/admin/users \
  -H "Authorization: Bearer $USER_TOKEN"

kill %1
```

Expected: Admin gets 200 with user list. Regular user gets 403.

- [ ] **Step 8: Verify SSH still working**

```bash
systemctl is-active ssh
ss -tlnp | grep 9453
```

Expected: SSH active on port 9453.

- [ ] **Step 9: Commit deploy.sh update**

```bash
git add scripts/deploy.sh
git commit -m "feat(deploy): add Phase 2 secrets to deploy script"
```

- [ ] **Step 10: Print summary**

```bash
echo ""
echo "============================================"
echo "  Lolday Backend Core — Phase 2 Complete"
echo "============================================"
echo ""
echo "Backend:     FastAPI $(kubectl -n lolday exec deploy/backend -- uv run python -c 'import fastapi; print(fastapi.__version__)' 2>/dev/null || echo 'running')"
echo "Database:    PostgreSQL 16"
echo "Cache:       Redis 7"
echo "Auth:        FastAPI Users (JWT)"
echo "Roles:       Admin, Developer, User"
echo "API Docs:    /docs (Swagger UI)"
echo "Health:      /api/v1/health"
echo ""
echo "Next: Phase 3 — Detector Lifecycle"
echo ""
```
