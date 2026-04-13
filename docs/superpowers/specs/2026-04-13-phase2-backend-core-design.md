# Phase 2: Backend Core — Design Specification

## Overview

FastAPI backend with PostgreSQL, Redis, JWT authentication, and role-based access control. Deployed as Kubernetes workloads managed by the existing Helm umbrella chart.

**Goal:** Users can register, log in, manage their profile, and admins can manage all users via REST API.

**Constraints:**
- All auth/user-management via FastAPI Users (no custom auth code)
- Async everywhere: asyncpg, async SQLAlchemy, async Redis
- All config from environment variables (Pydantic Settings)
- Backend image built locally, pushed to in-cluster registry:2

---

## Tech Stack

| Package | Version | Purpose |
|---------|---------|---------|
| fastapi | latest | Web framework |
| uvicorn[standard] | latest | ASGI server |
| fastapi-users[sqlalchemy] | latest | Auth + user management (JWT, bcrypt, CRUD) |
| sqlalchemy[asyncio] | latest | ORM (async) |
| asyncpg | latest | PostgreSQL async driver |
| alembic | latest | Database migrations |
| pydantic-settings | latest | Environment-based configuration |
| redis[hiredis] | latest | Async Redis client (token blacklist, rate limit) |
| slowapi | latest | Rate limiting middleware |
| httpx | latest | Test client |
| pytest + pytest-asyncio | latest | Testing |
| aiosqlite | latest | SQLite async driver (testing only) |

---

## Code Structure

```
backend/
├── pyproject.toml                # uv project, all dependencies
├── alembic.ini                   # DB migration config
├── alembic/
│   └── versions/                 # Migration files
├── app/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app + lifespan + include routers
│   ├── config.py                 # Pydantic Settings (env-based)
│   ├── db.py                     # async engine + sessionmaker + get_async_session
│   ├── models.py                 # User(SQLAlchemyBaseUserTableUUID) + Role enum
│   ├── schemas.py                # UserRead, UserCreate, UserUpdate
│   ├── users.py                  # FastAPI Users config (UserManager, JWT backend)
│   ├── deps.py                   # current_active_user, require_role()
│   └── routers/
│       ├── __init__.py
│       └── admin.py              # Admin-only: list all users
├── tests/
│   ├── conftest.py
│   └── test_auth.py
└── Dockerfile
```

Growth path for future phases:
- Phase 3: add `models/detector.py`, `routers/detectors.py`
- Phase 4: add `models/job.py`, `models/dataset.py`, `routers/jobs.py`, `routers/datasets.py`

When `models.py` or `schemas.py` grow too large, split into `models/` and `schemas/` directories.

---

## Database Schema

### User Model

FastAPI Users provides: `id` (UUID), `email`, `hashed_password`, `is_active`, `is_superuser`, `is_verified`.

Custom fields added:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| role | Enum(admin, developer, user) | user | RBAC role |
| display_name | String(100), nullable | null | Display name |
| created_at | Timestamp | now() | Registration time |

```python
class Role(str, enum.Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    USER = "user"

class User(SQLAlchemyBaseUserTableUUID, Base):
    role: Mapped[Role] = mapped_column(SAEnum(Role), default=Role.USER, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(default=func.now())
```

### Migration

Alembic with async support. Initial migration creates the `user` table.

---

## API Endpoints

All endpoints prefixed with `/api/v1`.

### Auth (FastAPI Users built-in)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | /auth/register | Register new user | Public |
| POST | /auth/login | Login, returns JWT | Public |
| POST | /auth/logout | Logout, blacklist token | Authenticated |
| POST | /auth/forgot-password | Request password reset | Public |
| POST | /auth/reset-password | Reset password with token | Token |
| POST | /auth/request-verify-token | Request email verification | Authenticated |
| POST | /auth/verify | Verify email with token | Token |

### Users (FastAPI Users built-in)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | /users/me | Get own profile | Authenticated |
| PATCH | /users/me | Update own profile | Authenticated |
| GET | /users/{id} | Get user by ID | Admin |
| PATCH | /users/{id} | Update user (incl. role) | Admin |
| DELETE | /users/{id} | Delete user | Admin |

### Admin (custom)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | /admin/users | List all users (paginated) | Admin |

### System

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | /health | Health check (DB + Redis) | Public |

### OpenAPI Docs

- `/docs` (Swagger UI) and `/redoc` enabled by default
- Disabled in production via `DOCS_ENABLED=false` environment variable

---

## Authentication & Authorization

### JWT Strategy

- Transport: Bearer token
- Strategy: JWT (via FastAPI Users)
- Token lifetime: 1 hour (configurable)
- Token blacklist: Redis (for logout/revocation)

### RBAC

Three roles: Admin > Developer > User.

Implemented as a FastAPI dependency:

```python
def require_role(min_role: Role):
    """Dependency that checks the user's role meets the minimum required."""
    ...
```

Role hierarchy: Admin has all permissions. Developer has Developer + User permissions. User has User permissions only.

### Rate Limiting

- slowapi with Redis backend
- Default: 60 requests/minute per IP
- Auth endpoints: 10 requests/minute per IP (prevent brute force)

---

## Kubernetes Deployment

### New Helm Templates

Added to `charts/lolday/templates/`:

| File | Resources |
|------|-----------|
| `postgresql.yaml` | StatefulSet + PVC + Service + Secret |
| `redis.yaml` | Deployment + Service |
| `backend.yaml` | Deployment + Service + ConfigMap |

### PostgreSQL

- Image: `postgres:16`
- StatefulSet with 1 replica
- PVC: 10Gi (K3s local-path StorageClass)
- Credentials in Secret (set via `--set` at deploy time)

### Redis

- Image: `redis:7-alpine`
- Deployment with 1 replica
- No persistence (used as cache/token store only)
- Data is ephemeral — restart clears token blacklist (acceptable: tokens expire naturally)

### Backend

- Image: `registry.lolday.svc.cluster.local:5000/lolday-backend:latest`
- Deployment with 1 replica
- ConfigMap for non-secret environment variables
- Secrets referenced from postgresql Secret + JWT secret
- Liveness probe: `GET /api/v1/health`
- Readiness probe: `GET /api/v1/health`

### values.yaml Additions

```yaml
backend:
  enabled: true
  image: registry.lolday.svc.cluster.local:5000/lolday-backend:latest
  replicas: 1
  env:
    DOCS_ENABLED: "true"

postgresql:
  enabled: true
  storage:
    size: 10Gi
  auth:
    database: lolday
    username: lolday
    password: ""          # --set at deploy time, NEVER commit

redis:
  enabled: true
```

### Backend Image Build

```bash
# Build
docker build -t localhost:5000/lolday-backend:latest backend/

# Push to in-cluster registry (requires port-forward)
kubectl -n lolday port-forward svc/registry 5000:5000 &
docker push localhost:5000/lolday-backend:latest
```

---

## Initial Admin Bootstrap

On first startup (app lifespan), if no users exist, create a seed admin:

- Email from `FIRST_ADMIN_EMAIL` env var
- Password from `FIRST_ADMIN_PASSWORD` env var
- Role: admin, is_superuser: true, is_verified: true
- Skipped if any user already exists

---

## Error Handling

- FastAPI built-in validation → 422
- FastAPI Users auth errors → 401/403
- slowapi rate limit → 429
- Custom exception handler for consistent format: `{"detail": "message"}`

---

## Testing Strategy

- pytest + pytest-asyncio + httpx (AsyncClient)
- Database: SQLite async (aiosqlite) — no PostgreSQL needed for tests
- Coverage: registration, login, token refresh, RBAC permission checks, rate limiting
- Run with: `pytest tests/`

---

## Phase Roadmap

| Phase | Name | Status |
|-------|------|--------|
| 1 | Infrastructure Foundation | Complete |
| 2 | Backend Core | Current |
| 3 | Detector Lifecycle | Pending design |
| 4 | Dataset & Jobs | Pending design |
| 5 | Frontend | Pending design |
| 6 | Operations | Pending design |
