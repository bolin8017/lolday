# Phase 3: Detector Lifecycle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver detector registration, sandboxed K8s Job build pipeline (Kaniko), Harbor with bundled Trivy CVE scanning, version management, and Pydantic config schema storage.

**Architecture:** FastAPI backend creates K8s Jobs for builds; an asyncio reconciler polls K8s Job + Harbor scan state; Kaniko builds detector images in non-privileged Pods; Harbor gates push with Trivy CVE scanning. Follows K3s + Flannel + kube-router (no Cilium; SSH safety) and cluster-internal HTTP.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Kubernetes Python client, httpx, cryptography (Fernet), Kaniko, Harbor 1.16, PostgreSQL 16, Redis 7.

**Spec:** `docs/superpowers/specs/2026-04-14-phase3-detector-lifecycle-design.md`

**Server:** server30 (Ubuntu 24.04, K3s v1.34.6+k3s1, 2× RTX 2080 Ti)

**Constraints:**

- `bolin8017` has no persistent sudo; give sudo commands to user to run
- CLI tools in `~/.local/bin/`; do NOT system-install anything without explicit approval
- SSH (port 9453) must never be disrupted; K3s must remain running after every step
- No Cilium / no CNI changes

---

## File Structure

Final backend Python layout:

```
backend/
├── pyproject.toml                    # + kubernetes, cryptography
├── alembic/
│   └── versions/
│       └── xxx_add_detector_tables.py  # NEW
├── app/
│   ├── main.py                       # MODIFY (routers + lifespan reconciler)
│   ├── config.py                     # MODIFY (Harbor/Fernet/Build env)
│   ├── db.py                         # unchanged
│   ├── deps.py                       # MODIFY (+ require_detector_access, require_build_token)
│   ├── users.py                      # unchanged
│   ├── reconciler.py                 # NEW
│   │
│   ├── models/                       # NEW (split from models.py)
│   │   ├── __init__.py               # re-export
│   │   ├── user.py
│   │   ├── detector.py               # Detector, DetectorVersion, DetectorBuild
│   │   └── credential.py
│   │
│   ├── schemas/                      # NEW (split from schemas.py)
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── detector.py
│   │   └── credential.py
│   │
│   ├── routers/
│   │   ├── admin.py                  # unchanged
│   │   ├── detectors.py              # NEW
│   │   ├── credentials.py            # NEW
│   │   └── internal.py               # NEW (build token schema callback)
│   │
│   └── services/                     # NEW
│       ├── __init__.py
│       ├── git.py                    # URL norm + GitHub API
│       ├── validator.py              # AST checks
│       ├── harbor.py                 # Harbor REST client
│       ├── build.py                  # K8s Job spec generation
│       ├── crypto.py                 # Fernet wrapper
│       └── k8s.py                    # K8s client singleton
│
└── tests/
    ├── conftest.py                   # MODIFY (add mocks for K8s, Harbor)
    ├── test_auth.py                  # existing
    ├── test_admin.py                 # existing
    ├── test_services_git.py          # NEW
    ├── test_services_validator.py    # NEW
    ├── test_services_crypto.py       # NEW
    ├── test_services_harbor.py       # NEW
    ├── test_services_build.py        # NEW
    ├── test_reconciler.py            # NEW
    ├── test_credentials.py           # NEW
    ├── test_detectors.py             # NEW
    ├── test_builds.py                # NEW
    └── test_internal.py              # NEW

charts/lolday/
├── Chart.yaml                        # MODIFY (+ harbor dep)
├── values.yaml                       # MODIFY (+ harbor/backend.env/secrets)
├── templates/
│   ├── backend.yaml                  # MODIFY (new env + SA)
│   ├── backend-rbac.yaml             # NEW (ServiceAccount + Role + RoleBinding)
│   ├── backend-fernet-secret.yaml    # NEW (from --set)
│   ├── harbor-admin-secret.yaml      # NEW (from --set)
│   ├── build-networkpolicy.yaml      # NEW
│   ├── registry.yaml                 # MODIFY (disable by default)
│   ├── postgresql.yaml               # unchanged
│   ├── redis.yaml                    # unchanged
│   ├── cloudflared.yaml              # unchanged
│   ├── network-policy.yaml           # unchanged
│   └── _helpers.tpl                  # unchanged
└── helpers/
    └── build-helper/                 # NEW (image source for validate container)
        ├── Dockerfile
        └── maldet_validator.py

scripts/
├── deploy.sh                         # MODIFY (+ helm dep, + harbor)
├── patch-k3s-registries.sh           # NEW
├── teardown.sh                       # unchanged
├── install-tools.sh                  # unchanged
└── setup-k3s.sh                      # unchanged
```

---

## Task 1: Backend Scaffolding — Dependencies + Split models.py / schemas.py

**Files:**

- Modify: `backend/pyproject.toml`
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/user.py`
- Create: `backend/app/models/detector.py` (placeholder for Task 2)
- Create: `backend/app/models/credential.py` (placeholder for Task 2)
- Create: `backend/app/schemas/__init__.py`
- Create: `backend/app/schemas/user.py`
- Create: `backend/app/schemas/detector.py` (placeholder for Task 2)
- Create: `backend/app/schemas/credential.py` (placeholder for Task 2)
- Delete: `backend/app/models.py`
- Delete: `backend/app/schemas.py`

- [ ] **Step 1: Add dependencies**

Edit `backend/pyproject.toml`, add to `dependencies`:

```toml
    "kubernetes>=31.0.0",
    "cryptography>=44.0.0",
    "httpx>=0.28.0",
```

(Note: `httpx` may already be in dev-dependencies from Phase 2; move or duplicate to main dependencies.)

Run:

```bash
cd backend && uv sync
```

Expected: new packages installed without errors.

- [ ] **Step 2: Create models/ package and split user model**

Create `backend/app/models/__init__.py`:

```python
from app.models.credential import UserGitCredential
from app.models.detector import Detector, DetectorBuild, DetectorVersion
from app.models.user import Base, Role, User

__all__ = [
    "Base",
    "Role",
    "User",
    "UserGitCredential",
    "Detector",
    "DetectorVersion",
    "DetectorBuild",
]
```

Create `backend/app/models/user.py` (content from existing `models.py`):

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

- [ ] **Step 3: Create placeholder detector.py and credential.py**

Create `backend/app/models/detector.py`:

```python
"""Detector lifecycle models — filled in Task 2."""
```

Create `backend/app/models/credential.py`:

```python
"""User git credential model — filled in Task 2."""
```

- [ ] **Step 4: Create schemas/ package and split user schemas**

Create `backend/app/schemas/__init__.py`:

```python
from app.schemas.user import AdminUserUpdate, UserCreate, UserRead, UserUpdate

__all__ = ["UserCreate", "UserRead", "UserUpdate", "AdminUserUpdate"]
```

Create `backend/app/schemas/user.py` (copy from existing `schemas.py`, verify content matches).

Create placeholder `backend/app/schemas/detector.py` and `backend/app/schemas/credential.py` each with just a docstring.

- [ ] **Step 5: Delete old single files**

```bash
rm backend/app/models.py backend/app/schemas.py
```

- [ ] **Step 6: Verify imports still work**

```bash
cd backend && uv run python -c "from app.main import app; print('OK')"
```

Expected: `OK`. If `ImportError`, check any remaining `from app.models import ...` or `from app.schemas import ...` in existing files (users.py, main.py, routers/admin.py) — those should resolve to `models/__init__.py` and `schemas/__init__.py` re-exports and continue to work unchanged.

- [ ] **Step 7: Run existing tests to confirm no regressions**

```bash
cd backend && uv run pytest
```

Expected: all Phase 2 tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/models backend/app/schemas
git rm backend/app/models.py backend/app/schemas.py
git commit -m "refactor(backend): split models.py and schemas.py into packages"
```

---

## Task 2: Detector Data Model + Alembic Migration

**Files:**

- Modify: `backend/app/models/detector.py`
- Modify: `backend/app/models/credential.py`
- Create: `backend/alembic/versions/xxxx_add_detector_tables.py`

- [ ] **Step 1: Write failing test for model field presence**

Create `backend/tests/test_models_detector.py`:

```python
from datetime import datetime
from uuid import UUID

from app.models.detector import Detector, DetectorBuild, DetectorVersion
from app.models.credential import UserGitCredential


def test_detector_has_required_fields():
    cols = {c.name for c in Detector.__table__.columns}
    assert cols >= {
        "id", "name", "display_name", "description", "git_url",
        "owner_id", "created_at", "deleted_at",
    }


def test_detector_version_has_required_fields():
    cols = {c.name for c in DetectorVersion.__table__.columns}
    assert cols >= {
        "id", "detector_id", "git_tag", "git_sha", "harbor_image",
        "image_digest", "config_schema", "built_at", "status",
    }


def test_detector_build_has_required_fields():
    cols = {c.name for c in DetectorBuild.__table__.columns}
    assert cols >= {
        "id", "detector_id", "git_tag", "git_sha", "triggered_by_id",
        "k8s_job_name", "status", "failure_reason", "log_tail",
        "trivy_critical", "trivy_high", "started_at", "finished_at",
    }


def test_user_git_credential_has_required_fields():
    cols = {c.name for c in UserGitCredential.__table__.columns}
    assert cols >= {
        "user_id", "provider", "encrypted_token", "token_hint",
        "created_at", "updated_at",
    }
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_models_detector.py -v
```

Expected: `ImportError` or `AttributeError` for missing classes.

- [ ] **Step 3: Implement `models/detector.py`**

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class DetectorVersionStatus(str, enum.Enum):
    ACTIVE = "active"
    RETENTION_PRUNED = "retention_pruned"


class DetectorBuildStatus(str, enum.Enum):
    PENDING = "pending"
    CLONING = "cloning"
    VALIDATING = "validating"
    BUILDING = "building"
    SCANNING = "scanning"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    CVE_BLOCKED = "cve_blocked"


class Detector(Base):
    __tablename__ = "detector"
    __table_args__ = (
        Index(
            "detector_owner_git_unique",
            "owner_id",
            "git_url",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    git_url: Mapped[str] = mapped_column(String(500), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DetectorVersion(Base):
    __tablename__ = "detector_version"
    __table_args__ = (
        UniqueConstraint("detector_id", "git_tag", name="detector_version_tag_unique"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    detector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("detector.id", ondelete="CASCADE"), nullable=False
    )
    git_tag: Mapped[str] = mapped_column(String(100), nullable=False)
    git_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    harbor_image: Mapped[str] = mapped_column(String(500), nullable=False)
    image_digest: Mapped[str] = mapped_column(String(100), nullable=False)
    config_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[DetectorVersionStatus] = mapped_column(
        SAEnum(DetectorVersionStatus, name="detector_version_status"),
        default=DetectorVersionStatus.ACTIVE,
        nullable=False,
    )


class DetectorBuild(Base):
    __tablename__ = "detector_build"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    detector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("detector.id", ondelete="CASCADE"), nullable=False
    )
    git_tag: Mapped[str] = mapped_column(String(100), nullable=False)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    triggered_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    k8s_job_name: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[DetectorBuildStatus] = mapped_column(
        SAEnum(DetectorBuildStatus, name="detector_build_status"),
        default=DetectorBuildStatus.PENDING,
        nullable=False,
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    log_tail: Mapped[str | None] = mapped_column(Text)
    trivy_critical: Mapped[int | None] = mapped_column(Integer)
    trivy_high: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Note: tests use SQLite (aiosqlite) which does not natively have `JSONB`; SQLAlchemy falls back to `JSON` for SQLite when using `JSONB`. For Postgres, `JSONB` is used. The column type still resolves to `JSON` at ORM layer, OK for tests.

- [ ] **Step 4: Implement `models/credential.py`**

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class GitProvider(str, enum.Enum):
    GITHUB = "github"
    GITLAB = "gitlab"


class UserGitCredential(Base):
    __tablename__ = "user_git_credential"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[GitProvider] = mapped_column(
        SAEnum(GitProvider, name="git_provider"), nullable=False
    )
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_hint: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 5: Update `models/__init__.py` to re-export enums**

```python
from app.models.credential import GitProvider, UserGitCredential
from app.models.detector import (
    Detector,
    DetectorBuild,
    DetectorBuildStatus,
    DetectorVersion,
    DetectorVersionStatus,
)
from app.models.user import Base, Role, User

__all__ = [
    "Base", "Role", "User",
    "GitProvider", "UserGitCredential",
    "Detector", "DetectorVersion", "DetectorVersionStatus",
    "DetectorBuild", "DetectorBuildStatus",
]
```

- [ ] **Step 6: Run the test**

```bash
cd backend && uv run pytest tests/test_models_detector.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 7: Generate Alembic migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "add detector tables"
```

Expected: new file in `alembic/versions/xxxx_add_detector_tables.py`. Open it, verify:

- Creates `detector`, `detector_version`, `detector_build`, `user_git_credential` tables
- Creates indices `detector_owner_git_unique` (partial), `detector_version_tag_unique`
- Creates ENUM types `detector_version_status`, `detector_build_status`, `git_provider`

If autogenerate produces extra/wrong ops (e.g. re-creating `user`), edit manually.

- [ ] **Step 8: Apply migration to test DB**

```bash
cd backend && uv run alembic upgrade head
```

Expected: migration runs without error. Check tables in test DB if possible.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models backend/alembic/versions backend/tests/test_models_detector.py
git commit -m "feat(backend): add detector lifecycle data models and migration"
```

---

## Task 3: Config, Crypto Service, Git Service

**Files:**

- Modify: `backend/app/config.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/crypto.py`
- Create: `backend/app/services/git.py`
- Create: `backend/tests/test_services_crypto.py`
- Create: `backend/tests/test_services_git.py`

- [ ] **Step 1: Add Phase 3 settings**

Modify `backend/app/config.py`, add to `Settings`:

```python
    # Phase 3: Detector Lifecycle
    FERNET_KEY: str = ""  # base64-encoded 32-byte Fernet key
    HARBOR_URL: str = "http://harbor.harbor.svc.cluster.local:80"
    HARBOR_ADMIN_USERNAME: str = "admin"
    HARBOR_ADMIN_PASSWORD: str = ""
    HARBOR_IMAGE_PREFIX: str = "harbor.harbor.svc:80"
    GITHUB_API_URL: str = "https://api.github.com"
    BUILD_NAMESPACE: str = "lolday"
    BUILD_IMAGE_HELPER: str = "harbor.harbor.svc:80/lolday/build-helper:v1"
    BUILD_IMAGE_KANIKO: str = "gcr.io/kaniko-project/executor:latest"
    BUILD_IMAGE_GIT: str = "alpine/git:2.45"
    BUILD_TIMEOUT_SECONDS: int = 1200
    BUILD_CONCURRENCY_PER_USER: int = 2
    BUILD_LOG_TAIL_BYTES: int = 8192
    REPO_MAX_SIZE_MB: int = 500
    BACKEND_INTERNAL_URL: str = "http://backend.lolday.svc:8000"
```

- [ ] **Step 2: Write failing test for crypto service**

Create `backend/tests/test_services_crypto.py`:

```python
import pytest
from cryptography.fernet import InvalidToken

from app.services.crypto import TokenCipher


def test_encrypt_decrypt_roundtrip():
    key = TokenCipher.generate_key()
    cipher = TokenCipher(key)
    plaintext = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    encrypted = cipher.encrypt(plaintext)
    assert isinstance(encrypted, bytes)
    assert cipher.decrypt(encrypted) == plaintext


def test_wrong_key_raises():
    key1 = TokenCipher.generate_key()
    key2 = TokenCipher.generate_key()
    encrypted = TokenCipher(key1).encrypt("hello")
    with pytest.raises(InvalidToken):
        TokenCipher(key2).decrypt(encrypted)


def test_hint_shows_prefix_and_suffix():
    assert TokenCipher.token_hint("ghp_abcdefghijklmnopqrstuvwxyz0123456789") == "ghp_...6789"
    assert TokenCipher.token_hint("short") == "sh...rt"
    assert TokenCipher.token_hint("a") == "a"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_services_crypto.py -v
```

Expected: `ImportError` for `TokenCipher`.

- [ ] **Step 4: Implement `services/crypto.py`**

Create `backend/app/services/__init__.py` (empty).

Create `backend/app/services/crypto.py`:

```python
from cryptography.fernet import Fernet


class TokenCipher:
    """Wraps Fernet symmetric encryption for user PATs."""

    def __init__(self, key: str | bytes) -> None:
        if isinstance(key, str):
            key = key.encode()
        self._fernet = Fernet(key)

    @staticmethod
    def generate_key() -> bytes:
        return Fernet.generate_key()

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        return self._fernet.decrypt(token).decode()

    @staticmethod
    def token_hint(token: str) -> str:
        """Human-readable hint that does not reveal the full token."""
        if len(token) <= 2:
            return token
        if len(token) <= 8:
            return f"{token[:2]}...{token[-2:]}"
        return f"{token[:4]}...{token[-4:]}"
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd backend && uv run pytest tests/test_services_crypto.py -v
```

Expected: 3 tests pass.

- [ ] **Step 6: Write failing test for git service**

Create `backend/tests/test_services_git.py`:

```python
import pytest

from app.services.git import normalize_git_url, parse_github_owner_repo


@pytest.mark.parametrize("raw,expected", [
    ("https://github.com/user/repo", "https://github.com/user/repo.git"),
    ("https://github.com/user/repo.git", "https://github.com/user/repo.git"),
    ("https://github.com/user/repo/", "https://github.com/user/repo.git"),
    ("git@github.com:user/repo.git", "https://github.com/user/repo.git"),
    ("git@github.com:user/repo", "https://github.com/user/repo.git"),
    ("http://github.com/user/repo", "https://github.com/user/repo.git"),
    ("HTTPS://GitHub.com/User/Repo", "https://github.com/User/Repo.git"),
])
def test_normalize_github_urls(raw, expected):
    assert normalize_git_url(raw) == expected


@pytest.mark.parametrize("bad", [
    "not a url",
    "https://example.com/foo/bar",  # non-GitHub host (v1 GitHub only)
    "https://github.com/only-one-segment",
    "",
])
def test_normalize_rejects_invalid(bad):
    with pytest.raises(ValueError):
        normalize_git_url(bad)


def test_parse_owner_repo():
    assert parse_github_owner_repo("https://github.com/user/repo.git") == ("user", "repo")
```

- [ ] **Step 7: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_services_git.py -v
```

Expected: `ImportError`.

- [ ] **Step 8: Implement `services/git.py`**

```python
import re
from urllib.parse import urlparse

_GITHUB_SSH_RE = re.compile(r"^git@github\.com:([^/]+)/(.+?)(?:\.git)?/?$", re.IGNORECASE)
_GITHUB_HTTPS_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", re.IGNORECASE
)


def normalize_git_url(raw: str) -> str:
    """Normalize any supported GitHub URL form to canonical HTTPS form.

    Supports: https(s)://, http(s)://, git@github.com:user/repo.git, trailing .git / slash variants.
    Only GitHub is supported in v1.
    """
    if not raw or not raw.strip():
        raise ValueError("empty git url")
    raw = raw.strip()

    m = _GITHUB_SSH_RE.match(raw)
    if m:
        owner, repo = m.group(1), m.group(2)
        return f"https://github.com/{owner}/{repo}.git"

    m = _GITHUB_HTTPS_RE.match(raw)
    if m:
        owner, repo = m.group(1), m.group(2)
        return f"https://github.com/{owner}/{repo}.git"

    raise ValueError(f"unsupported or invalid git url: {raw}")


def parse_github_owner_repo(normalized_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a normalized GitHub URL."""
    parsed = urlparse(normalized_url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2:
        raise ValueError(f"cannot parse owner/repo from {normalized_url}")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo
```

- [ ] **Step 9: Run tests**

```bash
cd backend && uv run pytest tests/test_services_git.py -v
```

Expected: all tests pass.

- [ ] **Step 10: Add GitHub API helper (list remote tags)**

Append to `backend/app/services/git.py`:

```python
import httpx

from app.config import settings


async def list_remote_tags(owner: str, repo: str, pat: str | None = None) -> list[dict]:
    """List tags via GitHub REST API. Returns [{'name': str, 'commit_sha': str}, ...].

    Uses unauthenticated requests when pat is None (for public repos); subject to
    lower GitHub rate limit (60 req/hour per IP). With pat, 5000 req/hour.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if pat:
        headers["Authorization"] = f"Bearer {pat}"
    async with httpx.AsyncClient(base_url=settings.GITHUB_API_URL, timeout=10) as client:
        resp = await client.get(f"/repos/{owner}/{repo}/tags", headers=headers)
        resp.raise_for_status()
        return [
            {"name": t["name"], "commit_sha": t["commit"]["sha"]}
            for t in resp.json()
        ]


async def check_repo_accessible(owner: str, repo: str, pat: str | None = None) -> bool:
    """Return True if the repo exists and is accessible with optional PAT."""
    headers = {"Accept": "application/vnd.github+json"}
    if pat:
        headers["Authorization"] = f"Bearer {pat}"
    async with httpx.AsyncClient(base_url=settings.GITHUB_API_URL, timeout=10) as client:
        resp = await client.get(f"/repos/{owner}/{repo}", headers=headers)
        return resp.status_code == 200
```

Tests for these helpers use `respx` (add to dev deps if missing) — defer to a later commit if needed. For Task 3 the URL norm test coverage is sufficient.

- [ ] **Step 11: Commit**

```bash
git add backend/app/config.py backend/app/services backend/tests/test_services_crypto.py backend/tests/test_services_git.py
git commit -m "feat(backend): add crypto (Fernet) and git URL/API services"
```

---

## Task 4: Static Validator Service

**Files:**

- Create: `backend/app/services/validator.py`
- Create: `backend/tests/test_services_validator.py`
- Create: `backend/tests/fixtures/valid_detector/` (minimal valid repo fixture)
- Create: `backend/tests/fixtures/invalid_detector_no_pyproject/`

- [ ] **Step 1: Create fixture directories**

```bash
mkdir -p backend/tests/fixtures/valid_detector
mkdir -p backend/tests/fixtures/invalid_detector_no_pyproject
```

Create `backend/tests/fixtures/valid_detector/pyproject.toml`:

```toml
[project]
name = "demo-detector"
version = "0.1.0"
description = "A demo detector"
requires-python = ">=3.12"
dependencies = ["islab-malware-detector>=0.4.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Create `backend/tests/fixtures/valid_detector/detector.py`:

```python
from maldet import BaseDetector, BaseDetectorConfig


class DemoConfig(BaseDetectorConfig):
    batch_size: int = 32


class DemoDetector(BaseDetector):
    config_class = DemoConfig

    def train(self):
        ...

    def evaluate(self):
        ...

    def predict(self):
        ...
```

Create `backend/tests/fixtures/valid_detector/Dockerfile`:

```dockerfile
FROM python:3.12-slim
COPY . /app
WORKDIR /app
RUN pip install .
```

Create `backend/tests/fixtures/invalid_detector_no_pyproject/detector.py`:

```python
from maldet import BaseDetector

class BadDetector(BaseDetector):
    pass
```

- [ ] **Step 2: Write failing test**

Create `backend/tests/test_services_validator.py`:

```python
from pathlib import Path

import pytest

from app.services.validator import StaticValidationError, validate_repo_static

FIXTURES = Path(__file__).parent / "fixtures"


def test_valid_detector_passes():
    validate_repo_static(FIXTURES / "valid_detector")


def test_missing_pyproject_rejected():
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(FIXTURES / "invalid_detector_no_pyproject")
    assert exc.value.code == "pyproject_missing"


def test_missing_dockerfile_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    (tmp_path / "detector.py").write_text("from maldet import BaseDetector\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "dockerfile_missing"


def test_missing_base_detector_import_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
    (tmp_path / "detector.py").write_text("class X: pass\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "base_detector_import_missing"


def test_unparseable_pyproject_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text("not-valid-toml = = = ")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "detector.py").write_text("from maldet import BaseDetector\n")
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "pyproject_unparseable"


def test_repo_too_large_rejected(tmp_path, monkeypatch):
    from app.services import validator as validator_mod
    monkeypatch.setattr(validator_mod, "REPO_MAX_SIZE_BYTES", 100)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "detector.py").write_text("from maldet import BaseDetector\n")
    (tmp_path / "big.bin").write_bytes(b"x" * 200)
    with pytest.raises(StaticValidationError) as exc:
        validate_repo_static(tmp_path)
    assert exc.value.code == "repo_too_large"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_services_validator.py -v
```

Expected: `ImportError`.

- [ ] **Step 4: Implement `services/validator.py`**

```python
import ast
import tomllib
from pathlib import Path

from app.config import settings

REPO_MAX_SIZE_BYTES = settings.REPO_MAX_SIZE_MB * 1024 * 1024


class StaticValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_repo_static(repo_root: Path) -> None:
    """Raise StaticValidationError on failure; return silently on success."""
    _check_size(repo_root)
    _check_pyproject(repo_root)
    _check_dockerfile(repo_root)
    _check_base_detector_import(repo_root)


def _check_size(repo_root: Path) -> None:
    total = 0
    for p in repo_root.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
            if total > REPO_MAX_SIZE_BYTES:
                raise StaticValidationError(
                    "repo_too_large",
                    f"repo exceeds {REPO_MAX_SIZE_BYTES} bytes",
                )


def _check_pyproject(repo_root: Path) -> None:
    pp = repo_root / "pyproject.toml"
    if not pp.is_file():
        raise StaticValidationError("pyproject_missing", "pyproject.toml not found")
    try:
        tomllib.loads(pp.read_text())
    except tomllib.TOMLDecodeError as e:
        raise StaticValidationError(
            "pyproject_unparseable", f"pyproject.toml is not valid TOML: {e}"
        ) from e


def _check_dockerfile(repo_root: Path) -> None:
    if not (repo_root / "Dockerfile").is_file():
        raise StaticValidationError(
            "dockerfile_missing", "Dockerfile required at repo root"
        )


def _check_base_detector_import(repo_root: Path) -> None:
    for py in repo_root.rglob("*.py"):
        # skip hidden dirs and common noise
        if any(part.startswith(".") or part in {"tests", "test"} for part in py.parts):
            continue
        try:
            tree = ast.parse(py.read_text(errors="ignore"), filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "maldet" in node.module:
                    for alias in node.names:
                        if alias.name == "BaseDetector":
                            return
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("maldet"):
                        return  # allow `import maldet; maldet.BaseDetector`
    raise StaticValidationError(
        "base_detector_import_missing",
        "no import of BaseDetector from maldet found",
    )
```

- [ ] **Step 5: Run tests**

```bash
cd backend && uv run pytest tests/test_services_validator.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/validator.py backend/tests/test_services_validator.py backend/tests/fixtures
git commit -m "feat(backend): add static detector repo validator"
```

---

## Task 5: Harbor API Client Service

**Files:**

- Create: `backend/app/services/harbor.py`
- Create: `backend/tests/test_services_harbor.py`
- Modify: `backend/pyproject.toml` (add `respx` to dev deps)

- [ ] **Step 1: Add respx**

Edit `backend/pyproject.toml` `dev-dependencies`:

```toml
    "respx>=0.21.0",
```

Run:

```bash
cd backend && uv sync
```

- [ ] **Step 2: Write failing test**

Create `backend/tests/test_services_harbor.py`:

```python
import httpx
import pytest
import respx

from app.services.harbor import HarborClient, ScanResult, ScanStatus


@pytest.mark.asyncio
async def test_ensure_project_creates_when_missing():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects", params={"name": "detectors"}).mock(
            return_value=httpx.Response(200, json=[])
        )
        mock.post("/api/v2.0/projects").mock(return_value=httpx.Response(201))
        client = HarborClient("http://harbor", "admin", "pw")
        await client.ensure_project("detectors", public=True)
        assert mock.calls.call_count == 2


@pytest.mark.asyncio
async def test_ensure_project_skips_when_exists():
    with respx.mock(base_url="http://harbor") as mock:
        mock.get("/api/v2.0/projects", params={"name": "detectors"}).mock(
            return_value=httpx.Response(200, json=[{"name": "detectors", "project_id": 1}])
        )
        client = HarborClient("http://harbor", "admin", "pw")
        await client.ensure_project("detectors", public=True)
        assert mock.calls.call_count == 1


@pytest.mark.asyncio
async def test_get_scan_parses_critical_high():
    with respx.mock(base_url="http://harbor") as mock:
        scan_body = {
            "application/vnd.security.vulnerability.report; version=1.1": {
                "scan_status": "Success",
                "severity": "Critical",
                "summary": {"summary": {"Critical": 2, "High": 5, "Medium": 10}},
            }
        }
        mock.get(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc/additions/vulnerabilities"
        ).mock(return_value=httpx.Response(200, json=scan_body))
        client = HarborClient("http://harbor", "admin", "pw")
        result = await client.get_scan("detectors", "foo", "sha256:abc")
        assert result.status == ScanStatus.SUCCESS
        assert result.critical == 2
        assert result.high == 5


@pytest.mark.asyncio
async def test_delete_artifact():
    with respx.mock(base_url="http://harbor") as mock:
        mock.delete(
            "/api/v2.0/projects/detectors/repositories/foo/artifacts/sha256:abc"
        ).mock(return_value=httpx.Response(200))
        client = HarborClient("http://harbor", "admin", "pw")
        await client.delete_artifact("detectors", "foo", "sha256:abc")
        assert mock.calls.call_count == 1
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_services_harbor.py -v
```

Expected: `ImportError`.

- [ ] **Step 4: Implement `services/harbor.py`**

```python
import enum
from dataclasses import dataclass

import httpx


class ScanStatus(str, enum.Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCESS = "Success"
    ERROR = "Error"
    NOT_SCANNED = "NotScanned"


@dataclass
class ScanResult:
    status: ScanStatus
    critical: int
    high: int
    medium: int
    low: int


class HarborClient:
    """Thin async client for Harbor REST v2.0."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = (username, password)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, auth=self._auth, timeout=15
        )

    async def ensure_project(self, name: str, public: bool = True) -> None:
        async with self._client() as c:
            resp = await c.get("/api/v2.0/projects", params={"name": name})
            resp.raise_for_status()
            existing = [p for p in resp.json() if p.get("name") == name]
            if existing:
                return
            create = await c.post(
                "/api/v2.0/projects",
                json={
                    "project_name": name,
                    "metadata": {"public": "true" if public else "false"},
                },
            )
            create.raise_for_status()

    async def ensure_robot_account(
        self, name: str, projects: list[str]
    ) -> dict:
        """Idempotent robot account creation. Returns {'name': ..., 'secret': ...} on creation,
        or {'name': ...} if already exists (secret cannot be retrieved later)."""
        async with self._client() as c:
            resp = await c.get("/api/v2.0/robots", params={"q": f"name={name}"})
            resp.raise_for_status()
            matches = [r for r in resp.json() if r.get("name", "").endswith(name)]
            if matches:
                return {"name": matches[0]["name"]}
            permissions = [
                {
                    "kind": "project",
                    "namespace": p,
                    "access": [
                        {"resource": "repository", "action": "pull"},
                        {"resource": "repository", "action": "push"},
                    ],
                }
                for p in projects
            ]
            create = await c.post(
                "/api/v2.0/robots",
                json={
                    "name": name,
                    "description": "lolday build pusher",
                    "disable": False,
                    "level": "system",
                    "duration": -1,
                    "permissions": permissions,
                },
            )
            create.raise_for_status()
            return create.json()

    async def set_retention_policy(
        self, project: str, keep_n_recent: int
    ) -> None:
        """Create or replace retention policy: keep N most recent tags."""
        async with self._client() as c:
            resp = await c.get(f"/api/v2.0/projects/{project}")
            resp.raise_for_status()
            project_id = resp.json()["project_id"]
            rule = {
                "algorithm": "or",
                "rules": [
                    {
                        "disabled": False,
                        "action": "retain",
                        "scope_selectors": {"repository": [{"kind": "doublestar", "decoration": "repoMatches", "pattern": "**"}]},
                        "tag_selectors": [{"kind": "doublestar", "decoration": "matches", "pattern": "**"}],
                        "params": {"latestPushedK": keep_n_recent},
                        "template": "latestPushedK",
                    }
                ],
                "trigger": {"kind": "Schedule", "settings": {"cron": "0 0 2 * * 0"}},
                "scope": {"level": "project", "ref": project_id},
            }
            existing = await c.get(f"/api/v2.0/retentions", params={"project_id": project_id})
            if existing.status_code == 200 and existing.json():
                policy_id = existing.json()[0]["id"]
                await (await c.put(f"/api/v2.0/retentions/{policy_id}", json=rule)).aread()
            else:
                await (await c.post("/api/v2.0/retentions", json=rule)).aread()

    async def get_artifact_digest(self, project: str, repo: str, tag: str) -> str | None:
        async with self._client() as c:
            resp = await c.get(
                f"/api/v2.0/projects/{project}/repositories/{repo}/artifacts/{tag}"
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json().get("digest")

    async def get_scan(self, project: str, repo: str, digest: str) -> ScanResult:
        async with self._client() as c:
            resp = await c.get(
                f"/api/v2.0/projects/{project}/repositories/{repo}/"
                f"artifacts/{digest}/additions/vulnerabilities"
            )
            resp.raise_for_status()
            body = resp.json()
            # Harbor returns a dict keyed by media type; take the first entry.
            if not body:
                return ScanResult(ScanStatus.NOT_SCANNED, 0, 0, 0, 0)
            report = next(iter(body.values()))
            status = ScanStatus(report.get("scan_status", "NotScanned"))
            summary = (report.get("summary") or {}).get("summary") or {}
            return ScanResult(
                status=status,
                critical=summary.get("Critical", 0),
                high=summary.get("High", 0),
                medium=summary.get("Medium", 0),
                low=summary.get("Low", 0),
            )

    async def delete_artifact(self, project: str, repo: str, digest: str) -> None:
        async with self._client() as c:
            resp = await c.delete(
                f"/api/v2.0/projects/{project}/repositories/{repo}/artifacts/{digest}"
            )
            if resp.status_code not in (200, 404):
                resp.raise_for_status()
```

- [ ] **Step 5: Run tests**

```bash
cd backend && uv run pytest tests/test_services_harbor.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/services/harbor.py backend/tests/test_services_harbor.py
git commit -m "feat(backend): add Harbor REST API client service"
```

---

## Task 6: K8s Client and Build Service

**Files:**

- Create: `backend/app/services/k8s.py`
- Create: `backend/app/services/build.py`
- Create: `backend/tests/test_services_build.py`

- [ ] **Step 1: Implement K8s client wrapper**

Create `backend/app/services/k8s.py`:

```python
from functools import lru_cache

from kubernetes import client, config


@lru_cache(maxsize=1)
def load_config() -> None:
    """Load in-cluster config (for running in Pod) or fallback to kubeconfig (local dev)."""
    try:
        config.load_incluster_config()
    except config.config_exception.ConfigException:
        config.load_kube_config()


def core_v1() -> client.CoreV1Api:
    load_config()
    return client.CoreV1Api()


def batch_v1() -> client.BatchV1Api:
    load_config()
    return client.BatchV1Api()
```

- [ ] **Step 2: Write failing test for build spec generator**

Create `backend/tests/test_services_build.py`:

```python
from uuid import uuid4

from app.services.build import (
    build_git_credential_secret,
    build_job_spec,
)


def test_job_spec_has_three_containers_and_security():
    build_id = uuid4()
    job = build_job_spec(
        build_id=build_id,
        detector_name="upxelfdet",
        git_tag="v0.1.0",
        owner_repo="bolin8017/upxelfdet",
    )
    spec = job["spec"]["template"]["spec"]

    # one kaniko main + two init containers
    assert len(spec["initContainers"]) == 2
    assert {c["name"] for c in spec["initContainers"]} == {"clone", "validate"}
    assert len(spec["containers"]) == 1
    assert spec["containers"][0]["name"] == "kaniko"

    # security
    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    for c in spec["initContainers"] + spec["containers"]:
        sc = c["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"]["drop"] == ["ALL"]

    # timeouts
    assert job["spec"]["activeDeadlineSeconds"] == 1200
    assert job["spec"]["ttlSecondsAfterFinished"] == 604800
    assert job["spec"]["backoffLimit"] == 0


def test_job_spec_kaniko_destination_matches_harbor_prefix():
    job = build_job_spec(
        build_id=uuid4(),
        detector_name="upxelfdet",
        git_tag="v0.1.0",
        owner_repo="bolin8017/upxelfdet",
    )
    kaniko = job["spec"]["template"]["spec"]["containers"][0]
    dest_arg = next(a for a in kaniko["args"] if a.startswith("--destination="))
    assert dest_arg.endswith("/detectors/upxelfdet:v0.1.0")


def test_git_credential_secret_contains_token_and_build_token():
    secret = build_git_credential_secret(
        build_id=uuid4(),
        username="bolin8017",
        pat_token="ghp_xxx",
        build_token="btok_abc",
    )
    assert secret["type"] == "Opaque"
    data = secret["stringData"]
    assert data["username"] == "bolin8017"
    assert data["token"] == "ghp_xxx"
    assert data["build_token"] == "btok_abc"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_services_build.py -v
```

Expected: `ImportError`.

- [ ] **Step 4: Implement `services/build.py`**

```python
import re
from typing import Any
from uuid import UUID

from app.config import settings


def _slugify(s: str) -> str:
    """K8s-safe slug (DNS-1123): lowercase alphanum + hyphen, max 63 chars."""
    s = re.sub(r"[^a-z0-9-]", "-", s.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:63]


def build_job_name(detector_name: str, git_tag: str, build_id: UUID) -> str:
    short_id = str(build_id).replace("-", "")[:8]
    return _slugify(f"build-{detector_name}-{git_tag}-{short_id}")


def build_secret_name(build_id: UUID) -> str:
    short_id = str(build_id).replace("-", "")[:8]
    return f"build-git-cred-{short_id}"


def build_git_credential_secret(
    build_id: UUID, username: str, pat_token: str, build_token: str
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": build_secret_name(build_id)},
        "type": "Opaque",
        "stringData": {
            "username": username,
            "token": pat_token,
            "build_token": build_token,
        },
    }


def build_job_spec(
    build_id: UUID,
    detector_name: str,
    git_tag: str,
    owner_repo: str,  # e.g. "bolin8017/upxelfdet"
) -> dict[str, Any]:
    job_name = build_job_name(detector_name, git_tag, build_id)
    secret_name = build_secret_name(build_id)
    destination = f"{settings.HARBOR_IMAGE_PREFIX}/detectors/{detector_name}:{git_tag}"
    cache_repo = f"{settings.HARBOR_IMAGE_PREFIX}/detectors-cache/{detector_name}"

    base_sc = {
        "allowPrivilegeEscalation": False,
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "capabilities": {"drop": ["ALL"]},
    }
    ro_sc = {**base_sc, "readOnlyRootFilesystem": True}
    # Kaniko writes to /kaniko; cannot be readOnlyRootFilesystem
    kaniko_sc = {**base_sc}

    pod_labels = {"app": "lolday-build", "lolday.io/build-id": str(build_id)}

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "labels": pod_labels},
        "spec": {
            "activeDeadlineSeconds": settings.BUILD_TIMEOUT_SECONDS,
            "ttlSecondsAfterFinished": 604800,
            "backoffLimit": 0,
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 1000,
                        "fsGroup": 1000,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumes": [
                        {"name": "workspace", "emptyDir": {"sizeLimit": "2Gi"}},
                        {
                            "name": "git-cred",
                            "secret": {"secretName": secret_name, "defaultMode": 0o400},
                        },
                        {
                            "name": "harbor-docker-cfg",
                            "secret": {
                                "secretName": "harbor-push-cred",
                                "items": [
                                    {"key": ".dockerconfigjson", "path": "config.json"}
                                ],
                                "defaultMode": 0o400,
                            },
                        },
                    ],
                    "initContainers": [
                        {
                            "name": "clone",
                            "image": settings.BUILD_IMAGE_GIT,
                            "command": ["/bin/sh", "-c"],
                            "args": [
                                "set +x; "
                                "git clone --depth=1 --recurse-submodules "
                                "--branch=\"$GIT_TAG\" "
                                "\"https://$GIT_USER:$GIT_TOKEN@github.com/$REPO.git\" "
                                "/workspace/src && "
                                "git -C /workspace/src rev-parse HEAD > /workspace/git-sha"
                            ],
                            "env": [
                                {"name": "GIT_TAG", "value": git_tag},
                                {"name": "REPO", "value": owner_repo},
                                {
                                    "name": "GIT_USER",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": secret_name, "key": "username"}
                                    },
                                },
                                {
                                    "name": "GIT_TOKEN",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": secret_name, "key": "token"}
                                    },
                                },
                            ],
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": "/workspace"}
                            ],
                            "securityContext": ro_sc,
                            "resources": {
                                "limits": {"cpu": "500m", "memory": "512Mi"}
                            },
                        },
                        {
                            "name": "validate",
                            "image": settings.BUILD_IMAGE_HELPER,
                            "command": ["python", "-m", "maldet_validator"],
                            "args": ["/workspace/src"],
                            "env": [
                                {"name": "BUILD_ID", "value": str(build_id)},
                                {
                                    "name": "BUILD_TOKEN",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": secret_name, "key": "build_token"}
                                    },
                                },
                                {"name": "BACKEND_URL", "value": settings.BACKEND_INTERNAL_URL},
                            ],
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": "/workspace"}
                            ],
                            "securityContext": ro_sc,
                            "resources": {"limits": {"cpu": "1", "memory": "1Gi"}},
                        },
                    ],
                    "containers": [
                        {
                            "name": "kaniko",
                            "image": settings.BUILD_IMAGE_KANIKO,
                            "args": [
                                "--context=dir:///workspace/src",
                                "--dockerfile=Dockerfile",
                                f"--destination={destination}",
                                "--cache=true",
                                f"--cache-repo={cache_repo}",
                                "--cache-ttl=336h",
                                "--snapshot-mode=redo",
                                "--log-format=json",
                                "--verbosity=info",
                            ],
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": "/workspace", "readOnly": True},
                                {"name": "harbor-docker-cfg", "mountPath": "/kaniko/.docker", "readOnly": True},
                            ],
                            "securityContext": kaniko_sc,
                            "resources": {
                                "requests": {"cpu": "1", "memory": "2Gi"},
                                "limits": {"cpu": "2", "memory": "4Gi"},
                            },
                        }
                    ],
                },
            },
        },
    }
```

- [ ] **Step 5: Run tests**

```bash
cd backend && uv run pytest tests/test_services_build.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/k8s.py backend/app/services/build.py backend/tests/test_services_build.py
git commit -m "feat(backend): add k8s client and build Job spec generator"
```

---

## Task 7: Credentials Router (PAT CRUD)

**Files:**

- Create: `backend/app/schemas/credential.py`
- Create: `backend/app/routers/credentials.py`
- Create: `backend/tests/test_credentials.py`

- [ ] **Step 1: Write Pydantic schemas**

Create `backend/app/schemas/credential.py`:

```python
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.credential import GitProvider


class GitCredentialSet(BaseModel):
    provider: GitProvider = GitProvider.GITHUB
    token: str = Field(min_length=8, max_length=200)


class GitCredentialRead(BaseModel):
    provider: GitProvider
    token_hint: str
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 2: Write failing test**

Create `backend/tests/test_credentials.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_set_credential_stores_encrypted(auth_client_user):
    resp = await auth_client_user.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_abcdefghij0123456789"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "github"
    assert body["token_hint"] == "ghp_...6789"


@pytest.mark.asyncio
async def test_get_credential_returns_hint_not_token(auth_client_user):
    await auth_client_user.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_abcdefghij0123456789"},
    )
    resp = await auth_client_user.get("/api/v1/users/me/git-credential")
    assert resp.status_code == 200
    body = resp.json()
    assert "token" not in body
    assert body["token_hint"].endswith("6789")


@pytest.mark.asyncio
async def test_delete_credential(auth_client_user):
    await auth_client_user.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_abcdefghij0123456789"},
    )
    resp = await auth_client_user.delete("/api/v1/users/me/git-credential")
    assert resp.status_code == 204
    resp2 = await auth_client_user.get("/api/v1/users/me/git-credential")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_rejected(client):
    resp = await client.get("/api/v1/users/me/git-credential")
    assert resp.status_code == 401
```

`auth_client_user` fixture: extend `conftest.py` to register a user and return an authenticated AsyncClient. If not already present from Phase 2, add to `conftest.py`:

```python
import pytest_asyncio


@pytest_asyncio.fixture
async def auth_client_user(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.dev", "password": "Password123!"},
    )
    login = await client.post(
        "/api/v1/auth/jwt/login",
        data={"username": "user@example.dev", "password": "Password123!"},
    )
    token = login.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_credentials.py -v
```

Expected: fixture or endpoint missing.

- [ ] **Step 4: Implement router**

Create `backend/app/routers/credentials.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.deps import current_active_user
from app.models import User
from app.models.credential import UserGitCredential
from app.schemas.credential import GitCredentialRead, GitCredentialSet
from app.services.crypto import TokenCipher

router = APIRouter()


def _cipher() -> TokenCipher:
    if not settings.FERNET_KEY:
        raise HTTPException(status_code=500, detail="FERNET_KEY not configured")
    return TokenCipher(settings.FERNET_KEY)


@router.put("/me/git-credential", response_model=GitCredentialRead)
async def set_credential(
    body: GitCredentialSet,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> GitCredentialRead:
    cipher = _cipher()
    existing = await session.get(UserGitCredential, user.id)
    encrypted = cipher.encrypt(body.token)
    hint = TokenCipher.token_hint(body.token)
    if existing:
        existing.provider = body.provider
        existing.encrypted_token = encrypted
        existing.token_hint = hint
    else:
        existing = UserGitCredential(
            user_id=user.id,
            provider=body.provider,
            encrypted_token=encrypted,
            token_hint=hint,
        )
        session.add(existing)
    await session.commit()
    await session.refresh(existing)
    return GitCredentialRead(
        provider=existing.provider,
        token_hint=existing.token_hint,
        created_at=existing.created_at,
        updated_at=existing.updated_at,
    )


@router.get("/me/git-credential", response_model=GitCredentialRead)
async def get_credential(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> GitCredentialRead:
    existing = await session.get(UserGitCredential, user.id)
    if not existing:
        raise HTTPException(status_code=404, detail="no credential set")
    return GitCredentialRead(
        provider=existing.provider,
        token_hint=existing.token_hint,
        created_at=existing.created_at,
        updated_at=existing.updated_at,
    )


@router.delete("/me/git-credential", status_code=204)
async def delete_credential(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    existing = await session.get(UserGitCredential, user.id)
    if existing:
        await session.delete(existing)
        await session.commit()
    return Response(status_code=204)
```

- [ ] **Step 5: Wire router into `main.py`**

Edit `backend/app/main.py`, add:

```python
from app.routers import admin, credentials
# ... existing includes ...
app.include_router(
    credentials.router,
    prefix="/api/v1/users",
    tags=["credentials"],
)
```

- [ ] **Step 6: Set FERNET_KEY for tests**

Edit `backend/tests/conftest.py` — ensure env is set before `Settings()` construction:

```python
import os

os.environ.setdefault(
    "FERNET_KEY",
    "dGVzdC1rZXktMzItYnl0ZXMtcGFkLWZvci1mZXJuZXQ=",  # base64 dummy test key (32 bytes)
)
```

Actually, generate a real one-time key:

```bash
cd backend && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy output, use in conftest.py. Example valid Fernet key: `ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=` (fine to commit — test-only).

- [ ] **Step 7: Run tests**

```bash
cd backend && uv run pytest tests/test_credentials.py -v
```

Expected: 4 tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas/credential.py backend/app/routers/credentials.py backend/app/main.py backend/tests/test_credentials.py backend/tests/conftest.py
git commit -m "feat(backend): add PAT credential management endpoints"
```

---

## Task 8: Detector Schemas + Register / List / Get / Delete Endpoints

**Files:**

- Modify: `backend/app/schemas/detector.py`
- Create: `backend/app/routers/detectors.py`
- Modify: `backend/app/deps.py` (+ require_detector_access)
- Create: `backend/tests/test_detectors.py`

- [ ] **Step 1: Write schemas**

Replace `backend/app/schemas/detector.py`:

```python
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.detector import DetectorBuildStatus, DetectorVersionStatus


class DetectorCreate(BaseModel):
    git_url: str
    name: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$|^[a-z0-9]$")
    display_name: str | None = None


class DetectorUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None


class DetectorRead(BaseModel):
    id: UUID
    name: str
    display_name: str
    description: str | None
    git_url: str
    owner_id: UUID
    created_at: datetime

    class Config:
        from_attributes = True


class VersionRead(BaseModel):
    id: UUID
    git_tag: str
    git_sha: str
    harbor_image: str
    image_digest: str
    built_at: datetime
    status: DetectorVersionStatus

    class Config:
        from_attributes = True


class VersionDetailRead(VersionRead):
    config_schema: dict[str, Any]


class BuildCreate(BaseModel):
    git_tag: str = Field(min_length=1, max_length=100)


class BuildRead(BaseModel):
    id: UUID
    detector_id: UUID
    git_tag: str
    git_sha: str | None
    status: DetectorBuildStatus
    failure_reason: str | None
    log_tail: str | None
    trivy_critical: int | None
    trivy_high: int | None
    started_at: datetime
    finished_at: datetime | None

    class Config:
        from_attributes = True


class AvailableTag(BaseModel):
    name: str
    commit_sha: str
```

- [ ] **Step 2: Add detector access dep**

Modify `backend/app/deps.py`, append:

```python
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.models import Role, User
from app.models.detector import Detector


async def load_detector(
    detector_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> Detector:
    d = await session.get(Detector, detector_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(status_code=404, detail="detector not found")
    return d


def require_detector_access(write: bool = False):
    """Build a dep that ensures caller is owner or admin.

    read (write=False): any authenticated user can read
    write (write=True): owner or admin only
    """
    from app.deps import current_active_user  # avoid circular at module load

    async def _inner(
        detector: Detector = Depends(load_detector),
        user: User = Depends(current_active_user),
    ) -> Detector:
        if not write:
            return detector
        if user.role == Role.ADMIN or detector.owner_id == user.id:
            return detector
        raise HTTPException(status_code=403, detail="not owner / admin")

    return _inner
```

- [ ] **Step 3: Write failing test for register / list / get / delete**

Create `backend/tests/test_detectors.py`:

```python
import pytest

# helpers from previous task fixtures: auth_client_user, auth_client_admin


@pytest.mark.asyncio
async def test_register_rejects_user_role(auth_client_user):
    resp = await auth_client_user.post(
        "/api/v1/detectors",
        json={"git_url": "https://github.com/bolin8017/upxelfdet"},
    )
    assert resp.status_code == 403  # user role cannot register


@pytest.mark.asyncio
async def test_register_developer(auth_client_developer, monkeypatch):
    # mock the clone + validate step (Task 8 register is synchronous)
    from app.routers import detectors as dr

    async def fake_clone_and_validate(url, pat):
        return {"name": "upxelfdet", "description": "demo", "display_name": "UPXELF"}

    monkeypatch.setattr(dr, "_clone_and_validate", fake_clone_and_validate)

    resp = await auth_client_developer.post(
        "/api/v1/detectors",
        json={"git_url": "https://github.com/bolin8017/upxelfdet"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "upxelfdet"


@pytest.mark.asyncio
async def test_register_duplicate_git_url(auth_client_developer, monkeypatch):
    from app.routers import detectors as dr
    monkeypatch.setattr(
        dr, "_clone_and_validate", _fake_meta("upxelfdet")
    )
    r1 = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    r2 = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    assert r1.status_code == 201
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_list_and_get(auth_client_developer, monkeypatch):
    from app.routers import detectors as dr
    monkeypatch.setattr(dr, "_clone_and_validate", _fake_meta("upxelfdet"))
    create = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    did = create.json()["id"]
    lst = await auth_client_developer.get("/api/v1/detectors")
    assert lst.status_code == 200
    assert any(d["id"] == did for d in lst.json()["items"])
    one = await auth_client_developer.get(f"/api/v1/detectors/{did}")
    assert one.status_code == 200


@pytest.mark.asyncio
async def test_soft_delete(auth_client_developer, monkeypatch):
    from app.routers import detectors as dr
    monkeypatch.setattr(dr, "_clone_and_validate", _fake_meta("upxelfdet"))
    create = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    did = create.json()["id"]
    d = await auth_client_developer.delete(f"/api/v1/detectors/{did}")
    assert d.status_code == 204
    g = await auth_client_developer.get(f"/api/v1/detectors/{did}")
    assert g.status_code == 404


def _fake_meta(name: str):
    async def _inner(url, pat):
        return {"name": name, "description": "demo", "display_name": name}
    return _inner
```

Add `auth_client_developer` and `auth_client_admin` fixtures to `conftest.py` (same pattern as `auth_client_user`, but set `role` via admin API after creation).

- [ ] **Step 4: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_detectors.py -v
```

Expected: `ImportError` or 404.

- [ ] **Step 5: Implement router**

Create `backend/app/routers/detectors.py`:

```python
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import current_active_user, require_role, require_detector_access
from app.models import Role, User
from app.models.credential import UserGitCredential
from app.models.detector import Detector
from app.schemas.detector import (
    DetectorCreate,
    DetectorRead,
    DetectorUpdate,
)
from app.services.crypto import TokenCipher
from app.services.git import (
    normalize_git_url,
    parse_github_owner_repo,
    check_repo_accessible,
)
from app.services.validator import StaticValidationError, validate_repo_static
from app.config import settings

router = APIRouter()


async def _get_user_pat(session: AsyncSession, user_id: UUID) -> str | None:
    cred = await session.get(UserGitCredential, user_id)
    if cred is None:
        return None
    return TokenCipher(settings.FERNET_KEY).decrypt(cred.encrypted_token)


async def _clone_and_validate(
    normalized_url: str, pat: str | None
) -> dict:
    """Synchronously clone shallow + run static validation.

    Returns metadata dict {name, description, display_name}.
    Raises HTTPException on failure.
    """
    owner, repo = parse_github_owner_repo(normalized_url)

    # Pre-flight: check repo accessibility via API
    ok = await check_repo_accessible(owner, repo, pat)
    if not ok:
        if pat is None:
            raise HTTPException(
                status_code=400,
                detail={"code": "credential_missing", "message": "repo not public; PAT required"},
            )
        raise HTTPException(
            status_code=400,
            detail={"code": "git_clone_failed", "message": "repo not accessible with PAT"},
        )

    tmpdir = tempfile.mkdtemp(prefix="lolday-register-")
    try:
        url_with_cred = (
            f"https://{pat}@github.com/{owner}/{repo}.git"
            if pat
            else f"https://github.com/{owner}/{repo}.git"
        )
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", url_with_cred, tmpdir,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail={"code": "git_clone_failed", "message": err.decode(errors="ignore")[:200]},
            )
        try:
            validate_repo_static(Path(tmpdir))
        except StaticValidationError as e:
            raise HTTPException(
                status_code=400, detail={"code": e.code, "message": e.message}
            )
        # Extract name + description from pyproject
        import tomllib
        data = tomllib.loads((Path(tmpdir) / "pyproject.toml").read_text())
        project = data.get("project", {})
        return {
            "name": project.get("name", repo).lower(),
            "description": project.get("description"),
            "display_name": project.get("name", repo),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@router.post("", response_model=DetectorRead, status_code=201)
async def register(
    body: DetectorCreate,
    user: User = Depends(require_role(Role.DEVELOPER)),
    session: AsyncSession = Depends(get_async_session),
) -> DetectorRead:
    try:
        normalized = normalize_git_url(body.git_url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_git_url", "message": str(e)})

    # Duplicate check (same owner+url and not deleted)
    dup = await session.execute(
        select(Detector).where(
            Detector.owner_id == user.id,
            Detector.git_url == normalized,
            Detector.deleted_at.is_(None),
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail={"code": "duplicate_registration", "message": "already registered by you"},
        )

    pat = await _get_user_pat(session, user.id)
    meta = await _clone_and_validate(normalized, pat)
    name = body.name or meta["name"]
    display_name = body.display_name or meta["display_name"]
    description = meta["description"]

    d = Detector(
        name=name,
        display_name=display_name,
        description=description,
        git_url=normalized,
        owner_id=user.id,
    )
    session.add(d)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "name_conflict", "message": f"detector name '{name}' already exists"},
        )
    await session.refresh(d)
    return DetectorRead.model_validate(d)


@router.get("")
async def list_detectors(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    owner_id: UUID | None = None,
    search: str | None = None,
    limit: Annotated[int, Query(le=100)] = 20,
    offset: int = 0,
) -> dict:
    stmt = select(Detector).where(Detector.deleted_at.is_(None))
    if owner_id:
        stmt = stmt.where(Detector.owner_id == owner_id)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(Detector.name.ilike(pattern))
    stmt = stmt.order_by(Detector.created_at.desc()).limit(limit).offset(offset)
    res = await session.execute(stmt)
    items = res.scalars().all()
    return {
        "items": [DetectorRead.model_validate(d).model_dump(mode="json") for d in items],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{detector_id}", response_model=DetectorRead)
async def get_detector(
    detector: Detector = Depends(require_detector_access(write=False)),
) -> DetectorRead:
    return DetectorRead.model_validate(detector)


@router.patch("/{detector_id}", response_model=DetectorRead)
async def update_detector(
    body: DetectorUpdate,
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> DetectorRead:
    if body.display_name is not None:
        detector.display_name = body.display_name
    if body.description is not None:
        detector.description = body.description
    await session.commit()
    await session.refresh(detector)
    return DetectorRead.model_validate(detector)


@router.delete("/{detector_id}", status_code=204)
async def delete_detector(
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    from datetime import datetime, timezone
    detector.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    # Harbor image cleanup: fire-and-forget task to delete all active versions
    from app.services.harbor import HarborClient
    harbor = HarborClient(
        settings.HARBOR_URL, settings.HARBOR_ADMIN_USERNAME, settings.HARBOR_ADMIN_PASSWORD
    )
    versions_res = await session.execute(
        select(DetectorVersion).where(DetectorVersion.detector_id == detector.id)
    )
    for v in versions_res.scalars().all():
        try:
            await harbor.delete_artifact("detectors", detector.name, v.image_digest)
            v.status = DetectorVersionStatus.RETENTION_PRUNED
        except Exception:
            pass  # best-effort cleanup; soft delete already succeeded
    await session.commit()
    return Response(status_code=204)
```

- [ ] **Step 6: Wire router in `main.py`**

Add:

```python
from app.routers import admin, credentials, detectors
app.include_router(detectors.router, prefix="/api/v1/detectors", tags=["detectors"])
```

- [ ] **Step 7: Run tests**

```bash
cd backend && uv run pytest tests/test_detectors.py -v
```

Expected: 5 tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas/detector.py backend/app/deps.py backend/app/routers/detectors.py backend/app/main.py backend/tests/test_detectors.py backend/tests/conftest.py
git commit -m "feat(backend): add detector register/list/get/update/delete endpoints"
```

---

## Task 9: Versions and Available-Tags Endpoints

**Files:**

- Modify: `backend/app/routers/detectors.py`
- Modify: `backend/tests/test_detectors.py` (or new `test_versions.py`)

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_versions.py`:

```python
import pytest
import respx
import httpx


@pytest.mark.asyncio
async def test_available_tags_calls_github(auth_client_developer, seed_detector, monkeypatch):
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/bolin8017/upxelfdet/tags").mock(
            return_value=httpx.Response(200, json=[
                {"name": "v0.1.0", "commit": {"sha": "abcdef1234"}},
                {"name": "v0.0.1", "commit": {"sha": "fedcba4321"}},
            ])
        )
        resp = await auth_client_developer.get(
            f"/api/v1/detectors/{seed_detector}/available-tags"
        )
        assert resp.status_code == 200
        tags = resp.json()
        assert len(tags) == 2
        assert tags[0]["name"] == "v0.1.0"


@pytest.mark.asyncio
async def test_versions_empty_initially(auth_client_developer, seed_detector):
    resp = await auth_client_developer.get(
        f"/api/v1/detectors/{seed_detector}/versions"
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": []}
```

Add `seed_detector` fixture to `conftest.py` that creates a detector via API (with mocked clone) and returns its id as UUID string.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/test_versions.py -v
```

Expected: 404 on endpoints.

- [ ] **Step 3: Implement endpoints**

Append to `backend/app/routers/detectors.py`:

```python
from app.schemas.detector import AvailableTag, VersionDetailRead, VersionRead
from app.models.detector import DetectorVersion, DetectorVersionStatus
from app.services.git import list_remote_tags


@router.get("/{detector_id}/versions")
async def list_versions(
    detector: Detector = Depends(require_detector_access(write=False)),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    res = await session.execute(
        select(DetectorVersion)
        .where(DetectorVersion.detector_id == detector.id)
        .where(DetectorVersion.status == DetectorVersionStatus.ACTIVE)
        .order_by(DetectorVersion.built_at.desc())
    )
    versions = res.scalars().all()
    return {
        "items": [VersionRead.model_validate(v).model_dump(mode="json") for v in versions]
    }


@router.get("/{detector_id}/versions/{tag}", response_model=VersionDetailRead)
async def get_version(
    tag: str,
    detector: Detector = Depends(require_detector_access(write=False)),
    session: AsyncSession = Depends(get_async_session),
) -> VersionDetailRead:
    res = await session.execute(
        select(DetectorVersion).where(
            DetectorVersion.detector_id == detector.id,
            DetectorVersion.git_tag == tag,
        )
    )
    version = res.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    return VersionDetailRead.model_validate(version)


@router.get("/{detector_id}/available-tags")
async def available_tags(
    detector: Detector = Depends(require_detector_access(write=True)),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[AvailableTag]:
    owner, repo = parse_github_owner_repo(detector.git_url)
    pat = await _get_user_pat(session, user.id)
    tags = await list_remote_tags(owner, repo, pat)
    return [AvailableTag(name=t["name"], commit_sha=t["commit_sha"]) for t in tags]
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/test_versions.py -v
```

Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/detectors.py backend/tests/test_versions.py backend/tests/conftest.py
git commit -m "feat(backend): add version list and GitHub available-tags endpoints"
```

---

## Task 10: Build Lifecycle Endpoints + Internal Schema Callback

**Files:**

- Create: `backend/app/routers/internal.py`
- Modify: `backend/app/routers/detectors.py`
- Modify: `backend/app/deps.py` (+ require_build_token)
- Create: `backend/tests/test_builds.py`
- Create: `backend/tests/test_internal.py`

- [ ] **Step 1: Add build token dep**

Append to `backend/app/deps.py`:

```python
import secrets
from fastapi import Header
from sqlalchemy import select

from app.models.detector import DetectorBuild


def generate_build_token() -> str:
    return f"btok_{secrets.token_urlsafe(32)}"


async def require_build_token(
    build_id: UUID,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> DetectorBuild:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:]
    build = await session.get(DetectorBuild, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    # Build token stored in the k8s secret name field OR separate column. For
    # simplicity we store it in failure_reason-style column? NO — create a
    # dedicated column in migration Task 2 if not done. For Phase 3 we reuse
    # k8s_job_name convention: token stored separately via DB. See note below.
    # Here we assume build.build_token column exists:
    if not hasattr(build, "build_token") or build.build_token != token:
        raise HTTPException(status_code=401, detail="invalid build token")
    if build.status not in {DetectorBuildStatus.VALIDATING, DetectorBuildStatus.BUILDING}:
        raise HTTPException(status_code=400, detail="build not in schema-accepting state")
    return build
```

- [ ] **Step 2: Adjust model — add `build_token` column**

Go back to `backend/app/models/detector.py`, add to `DetectorBuild`:

```python
    build_token: Mapped[str | None] = mapped_column(String(80))
```

Generate a new Alembic migration:

```bash
cd backend && uv run alembic revision --autogenerate -m "add build_token column"
cd backend && uv run alembic upgrade head
```

- [ ] **Step 3: Write failing tests for build create / list / get**

Create `backend/tests/test_builds.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_create_build_triggers_k8s_job(auth_client_developer, seed_detector, monkeypatch):
    fake_k8s = MagicMock()
    fake_k8s.create_namespaced_secret = MagicMock()
    fake_k8s.create_namespaced_job = MagicMock()
    monkeypatch.setattr("app.routers.detectors._create_k8s_resources", AsyncMock(return_value="build-xxx-123"))

    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds",
        json={"git_tag": "v0.1.0"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["git_tag"] == "v0.1.0"
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_duplicate_in_flight_build_returns_409(auth_client_developer, seed_detector, monkeypatch):
    monkeypatch.setattr("app.routers.detectors._create_k8s_resources", AsyncMock(return_value="build-xxx-123"))
    await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds", json={"git_tag": "v0.1.0"}
    )
    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds", json={"git_tag": "v0.1.0"}
    )
    assert resp.status_code == 409
    assert "existing" in resp.json()["detail"].get("message", "").lower() or resp.json()["detail"].get("code") == "build_in_flight"


@pytest.mark.asyncio
async def test_per_user_concurrency_cap(auth_client_developer, seed_detector, monkeypatch):
    monkeypatch.setattr("app.routers.detectors._create_k8s_resources", AsyncMock(return_value="build-xxx-123"))
    monkeypatch.setenv("BUILD_CONCURRENCY_PER_USER", "1")
    # Create first build — OK
    r1 = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds", json={"git_tag": "v0.1.0"}
    )
    assert r1.status_code == 201
    # Different tag, same user, should hit cap
    r2 = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds", json={"git_tag": "v0.2.0"}
    )
    assert r2.status_code == 429
```

Create `backend/tests/test_internal.py`:

```python
import pytest

from app.models.detector import DetectorBuildStatus


@pytest.mark.asyncio
async def test_schema_callback_with_valid_token(db_session, seed_build_with_token, client):
    build_id, token = seed_build_with_token
    resp = await client.post(
        f"/api/v1/internal/builds/{build_id}/schema",
        json={"schema": {"type": "object", "properties": {}}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_schema_callback_rejects_bad_token(db_session, seed_build_with_token, client):
    build_id, _ = seed_build_with_token
    resp = await client.post(
        f"/api/v1/internal/builds/{build_id}/schema",
        json={"schema": {}},
        headers={"Authorization": "Bearer bad"},
    )
    assert resp.status_code == 401
```

(`seed_build_with_token` fixture creates a DetectorBuild directly in the DB with status=VALIDATING, build_token set.)

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_builds.py tests/test_internal.py -v
```

Expected: route missing.

- [ ] **Step 5: Implement build creation + list + get + cancel**

Append to `backend/app/routers/detectors.py`:

```python
from fastapi import Response
from kubernetes.client import ApiException

from app.deps import generate_build_token
from app.models.detector import DetectorBuild, DetectorBuildStatus
from app.schemas.detector import BuildCreate, BuildRead
from app.services.build import (
    build_job_name,
    build_git_credential_secret,
    build_job_spec,
    build_secret_name,
)
from app.services.k8s import batch_v1, core_v1


async def _create_k8s_resources(
    build_id,
    detector: Detector,
    git_tag: str,
    pat: str,
    build_token: str,
) -> str:
    """Create Secret + Job. Returns job name. Separated so tests can monkeypatch."""
    owner, repo = parse_github_owner_repo(detector.git_url)
    owner_repo = f"{owner}/{repo}"
    secret = build_git_credential_secret(
        build_id=build_id,
        username=owner,
        pat_token=pat,
        build_token=build_token,
    )
    core_v1().create_namespaced_secret(
        namespace=settings.BUILD_NAMESPACE, body=secret
    )
    job = build_job_spec(
        build_id=build_id,
        detector_name=detector.name,
        git_tag=git_tag,
        owner_repo=owner_repo,
    )
    try:
        batch_v1().create_namespaced_job(
            namespace=settings.BUILD_NAMESPACE, body=job
        )
    except ApiException:
        # Rollback Secret if Job creation fails
        core_v1().delete_namespaced_secret(
            name=build_secret_name(build_id), namespace=settings.BUILD_NAMESPACE
        )
        raise
    return build_job_name(detector.name, git_tag, build_id)


@router.post("/{detector_id}/builds", response_model=BuildRead, status_code=201)
async def create_build(
    body: BuildCreate,
    detector: Detector = Depends(require_detector_access(write=True)),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> BuildRead:
    # PAT required (even public repos: avoids GitHub rate limits + keeps flow uniform)
    pat = await _get_user_pat(session, user.id)
    if pat is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "credential_missing", "message": "set Git PAT first"},
        )

    # Idempotency: in-flight build for same detector+tag
    in_flight_statuses = [
        DetectorBuildStatus.PENDING,
        DetectorBuildStatus.CLONING,
        DetectorBuildStatus.VALIDATING,
        DetectorBuildStatus.BUILDING,
        DetectorBuildStatus.SCANNING,
    ]
    existing = await session.execute(
        select(DetectorBuild).where(
            DetectorBuild.detector_id == detector.id,
            DetectorBuild.git_tag == body.git_tag,
            DetectorBuild.status.in_(in_flight_statuses),
        )
    )
    existing_build = existing.scalar_one_or_none()
    if existing_build:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "build_in_flight",
                "message": f"existing in-flight build {existing_build.id}",
                "build_id": str(existing_build.id),
            },
        )

    # Concurrency cap
    active_count = await session.execute(
        select(func.count()).select_from(DetectorBuild).where(
            DetectorBuild.triggered_by_id == user.id,
            DetectorBuild.status.in_(in_flight_statuses),
        )
    )
    if active_count.scalar() >= settings.BUILD_CONCURRENCY_PER_USER:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "concurrency_cap",
                "message": f"max {settings.BUILD_CONCURRENCY_PER_USER} in-flight builds per user",
            },
        )

    # Persist build row first (PENDING), then create K8s resources
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag=body.git_tag,
        triggered_by_id=user.id,
        status=DetectorBuildStatus.PENDING,
        build_token=generate_build_token(),
    )
    session.add(build)
    await session.commit()
    await session.refresh(build)

    try:
        job_name = await _create_k8s_resources(
            build_id=build.id,
            detector=detector,
            git_tag=body.git_tag,
            pat=pat,
            build_token=build.build_token,
        )
    except Exception as e:
        build.status = DetectorBuildStatus.FAILED
        build.failure_reason = f"k8s_error: {type(e).__name__}: {e}"
        await session.commit()
        raise HTTPException(status_code=500, detail="failed to launch build job")

    build.k8s_job_name = job_name
    build.status = DetectorBuildStatus.CLONING
    await session.commit()
    await session.refresh(build)
    return BuildRead.model_validate(build)


@router.get("/{detector_id}/builds")
async def list_builds(
    detector: Detector = Depends(require_detector_access(write=False)),
    session: AsyncSession = Depends(get_async_session),
    limit: int = 20,
    offset: int = 0,
) -> dict:
    res = await session.execute(
        select(DetectorBuild)
        .where(DetectorBuild.detector_id == detector.id)
        .order_by(DetectorBuild.started_at.desc())
        .limit(limit).offset(offset)
    )
    builds = res.scalars().all()
    return {
        "items": [BuildRead.model_validate(b).model_dump(mode="json") for b in builds],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{detector_id}/builds/{build_id}", response_model=BuildRead)
async def get_build(
    build_id: UUID,
    detector: Detector = Depends(require_detector_access(write=False)),
    session: AsyncSession = Depends(get_async_session),
) -> BuildRead:
    build = await session.get(DetectorBuild, build_id)
    if build is None or build.detector_id != detector.id:
        raise HTTPException(status_code=404, detail="build not found")
    return BuildRead.model_validate(build)


@router.post("/{detector_id}/builds/{build_id}/cancel", status_code=204)
async def cancel_build(
    build_id: UUID,
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    build = await session.get(DetectorBuild, build_id)
    if build is None or build.detector_id != detector.id:
        raise HTTPException(status_code=404, detail="build not found")
    if build.k8s_job_name:
        try:
            batch_v1().delete_namespaced_job(
                name=build.k8s_job_name,
                namespace=settings.BUILD_NAMESPACE,
                propagation_policy="Background",
            )
        except ApiException:
            pass
    build.status = DetectorBuildStatus.CANCELLED
    await session.commit()
    return Response(status_code=204)
```

Note: import `from sqlalchemy import func` at top of file.

- [ ] **Step 6: Implement internal schema callback**

Create `backend/app/routers/internal.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import require_build_token
from app.models.detector import DetectorBuild

router = APIRouter()


@router.post("/builds/{build_id}/schema")
async def submit_schema(
    payload: dict,
    build: DetectorBuild = Depends(require_build_token),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Called by validate init container with the Pydantic JSON schema + git_sha.

    Expected payload: {"schema": <dict>, "git_sha": "<40-char-sha>"}
    """
    if "schema" not in payload:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="missing 'schema' in payload")
    build.pending_schema = payload["schema"]
    if payload.get("git_sha"):
        build.git_sha = payload["git_sha"]
    await session.commit()
    return {"accepted": True}
```

Add column to `DetectorBuild` in `models/detector.py`:

```python
    pending_schema: Mapped[dict | None] = mapped_column(JSONB)
```

Generate migration:

```bash
cd backend && uv run alembic revision --autogenerate -m "add pending_schema column"
cd backend && uv run alembic upgrade head
```

Wire router in `main.py`:

```python
from app.routers import admin, credentials, detectors, internal
app.include_router(internal.router, prefix="/api/v1/internal", tags=["internal"])
```

- [ ] **Step 7: Run tests**

```bash
cd backend && uv run pytest tests/test_builds.py tests/test_internal.py -v
```

Expected: all pass. Adjust mocks/fixtures as needed.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/detector.py backend/app/deps.py backend/app/routers backend/app/main.py backend/alembic/versions backend/tests/test_builds.py backend/tests/test_internal.py
git commit -m "feat(backend): add build lifecycle endpoints and schema callback"
```

---

## Task 11: Build Reconciler

**Files:**

- Create: `backend/app/reconciler.py`
- Create: `backend/tests/test_reconciler.py`
- Modify: `backend/app/main.py` (lifespan)

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_reconciler.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.detector import DetectorBuild, DetectorBuildStatus


@pytest.mark.asyncio
async def test_reconcile_succeeded_job_moves_to_scanning(db_session):
    from app.reconciler import reconcile_build
    build = DetectorBuild(
        detector_id=uuid4(),
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-foo-abc",
        status=DetectorBuildStatus.BUILDING,
        build_token="btok_x",
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    with patch("app.reconciler.batch_v1") as bv:
        bv.return_value.read_namespaced_job.return_value = fake_job
        # harbor scan pending
        with patch("app.reconciler.HarborClient") as hc:
            hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:abc")
            from app.services.harbor import ScanResult, ScanStatus
            hc.return_value.get_scan = AsyncMock(
                return_value=ScanResult(ScanStatus.PENDING, 0, 0, 0, 0)
            )
            await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.SCANNING


@pytest.mark.asyncio
async def test_reconcile_cve_blocked(db_session):
    from app.reconciler import reconcile_build
    build = DetectorBuild(
        detector_id=uuid4(),
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-foo-xyz",
        status=DetectorBuildStatus.BUILDING,
        build_token="btok_y",
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    with patch("app.reconciler.batch_v1") as bv, \
         patch("app.reconciler.HarborClient") as hc, \
         patch("app.reconciler.core_v1") as cv:
        bv.return_value.read_namespaced_job.return_value = fake_job
        from app.services.harbor import ScanResult, ScanStatus
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:deadbeef")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.SUCCESS, critical=1, high=0, medium=0, low=0)
        )
        hc.return_value.delete_artifact = AsyncMock()
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.CVE_BLOCKED
    assert build.trivy_critical == 1
    assert build.finished_at is not None


@pytest.mark.asyncio
async def test_reconcile_timeout(db_session):
    from datetime import datetime, timedelta, timezone
    from app.reconciler import reconcile_build

    build = DetectorBuild(
        detector_id=uuid4(),
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-foo-timeout",
        status=DetectorBuildStatus.BUILDING,
        build_token="btok_z",
    )
    # started_at far in the past
    build.started_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 0
    fake_job.status.failed = 0

    with patch("app.reconciler.batch_v1") as bv, patch("app.reconciler.core_v1"):
        bv.return_value.read_namespaced_job.return_value = fake_job
        bv.return_value.delete_namespaced_job.return_value = None
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.TIMEOUT
    assert build.finished_at is not None
```

- [ ] **Step 2: Implement `reconciler.py`**

```python
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from kubernetes.client import ApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session_maker
from app.models.detector import (
    DetectorBuild,
    DetectorBuildStatus,
    DetectorVersion,
    DetectorVersionStatus,
)
from app.services.build import build_secret_name
from app.services.harbor import HarborClient, ScanResult, ScanStatus
from app.services.k8s import batch_v1, core_v1

logger = logging.getLogger(__name__)

IN_FLIGHT = {
    DetectorBuildStatus.PENDING,
    DetectorBuildStatus.CLONING,
    DetectorBuildStatus.VALIDATING,
    DetectorBuildStatus.BUILDING,
    DetectorBuildStatus.SCANNING,
}


async def reconcile_build(session: AsyncSession, b: DetectorBuild) -> None:
    try:
        job = batch_v1().read_namespaced_job(
            name=b.k8s_job_name, namespace=settings.BUILD_NAMESPACE
        )
    except ApiException as e:
        if e.status == 404:
            b.status = DetectorBuildStatus.FAILED
            b.failure_reason = "k8s_job_missing"
            b.finished_at = datetime.now(timezone.utc)
            await session.commit()
        return

    if job.status.succeeded:
        await _handle_succeeded(session, b)
    elif job.status.failed:
        await _handle_failed(session, b, job)
    elif (datetime.now(timezone.utc) - b.started_at.replace(tzinfo=timezone.utc)).total_seconds() \
            > settings.BUILD_TIMEOUT_SECONDS + 60:
        await _handle_timeout(session, b)
    else:
        await _update_progress(session, b, job)


async def _handle_succeeded(session: AsyncSession, b: DetectorBuild) -> None:
    from app.models.detector import Detector
    detector = await session.get(Detector, b.detector_id)
    harbor = HarborClient(
        settings.HARBOR_URL, settings.HARBOR_ADMIN_USERNAME, settings.HARBOR_ADMIN_PASSWORD
    )
    digest = await harbor.get_artifact_digest("detectors", detector.name, b.git_tag)
    if digest is None:
        b.status = DetectorBuildStatus.FAILED
        b.failure_reason = "artifact_missing_in_harbor"
        b.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return

    scan = await harbor.get_scan("detectors", detector.name, digest)
    if scan.status in {ScanStatus.PENDING, ScanStatus.RUNNING, ScanStatus.NOT_SCANNED}:
        b.status = DetectorBuildStatus.SCANNING
        await session.commit()
        return

    if scan.critical > 0 or scan.high > 0:
        await harbor.delete_artifact("detectors", detector.name, digest)
        b.status = DetectorBuildStatus.CVE_BLOCKED
        b.failure_reason = f"cve_blocked: critical={scan.critical} high={scan.high}"
        b.trivy_critical = scan.critical
        b.trivy_high = scan.high
        b.finished_at = datetime.now(timezone.utc)
        await session.commit()
    else:
        # record version
        version = DetectorVersion(
            detector_id=b.detector_id,
            git_tag=b.git_tag,
            git_sha=await _read_git_sha_from_log(b),
            harbor_image=f"{settings.HARBOR_IMAGE_PREFIX}/detectors/{detector.name}:{b.git_tag}",
            image_digest=digest,
            config_schema=b.pending_schema or {},
            status=DetectorVersionStatus.ACTIVE,
        )
        session.add(version)
        b.status = DetectorBuildStatus.SUCCEEDED
        b.git_sha = version.git_sha
        b.trivy_critical = 0
        b.trivy_high = 0
        b.finished_at = datetime.now(timezone.utc)
        await session.commit()
    await _cleanup_build_secret(b.id)


async def _handle_failed(session: AsyncSession, b: DetectorBuild, job) -> None:
    reason = await _extract_failure_reason(b)
    b.status = DetectorBuildStatus.FAILED
    b.failure_reason = reason
    b.log_tail = await _capture_log_tail(b)
    b.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await _cleanup_build_secret(b.id)


async def _handle_timeout(session: AsyncSession, b: DetectorBuild) -> None:
    try:
        batch_v1().delete_namespaced_job(
            name=b.k8s_job_name,
            namespace=settings.BUILD_NAMESPACE,
            propagation_policy="Background",
        )
    except ApiException:
        pass
    b.status = DetectorBuildStatus.TIMEOUT
    b.failure_reason = "build exceeded timeout"
    b.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await _cleanup_build_secret(b.id)


async def _update_progress(session: AsyncSession, b: DetectorBuild, job) -> None:
    """Update status based on which init container is running."""
    pods = core_v1().list_namespaced_pod(
        namespace=settings.BUILD_NAMESPACE,
        label_selector=f"lolday.io/build-id={b.id}",
    )
    if not pods.items:
        return
    pod = pods.items[0]
    init_statuses = pod.status.init_container_statuses or []
    finished = {ic.name for ic in init_statuses if ic.state.terminated}
    if "validate" in finished:
        b.status = DetectorBuildStatus.BUILDING
    elif "clone" in finished:
        b.status = DetectorBuildStatus.VALIDATING
    else:
        b.status = DetectorBuildStatus.CLONING
    await session.commit()


async def _capture_log_tail(b: DetectorBuild) -> str:
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.BUILD_NAMESPACE,
            label_selector=f"lolday.io/build-id={b.id}",
        )
        if not pods.items:
            return ""
        pod = pods.items[0]
        # Combine kaniko logs (main container) if available
        log = core_v1().read_namespaced_pod_log(
            name=pod.metadata.name,
            namespace=settings.BUILD_NAMESPACE,
            container="kaniko",
            tail_lines=200,
        )
        return log[-settings.BUILD_LOG_TAIL_BYTES:]
    except ApiException:
        return ""


async def _extract_failure_reason(b: DetectorBuild) -> str:
    """Examine pod's init containers to determine which step failed."""
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.BUILD_NAMESPACE,
            label_selector=f"lolday.io/build-id={b.id}",
        )
        if not pods.items:
            return "pod_missing"
        pod = pods.items[0]
        for ic in (pod.status.init_container_statuses or []):
            if ic.state.terminated and ic.state.terminated.exit_code != 0:
                return f"{ic.name}_failed: exit={ic.state.terminated.exit_code}"
        for cs in (pod.status.container_statuses or []):
            if cs.state.terminated and cs.state.terminated.exit_code != 0:
                return f"{cs.name}_failed: exit={cs.state.terminated.exit_code}"
        return "unknown_failure"
    except ApiException:
        return "k8s_api_error"


async def _read_git_sha_from_log(b: DetectorBuild) -> str:
    """git_sha is populated on build row by the validate container's schema callback
    (see Task 10 /internal/builds/{id}/schema — payload includes git_sha)."""
    return b.git_sha or ""


async def _cleanup_build_secret(build_id) -> None:
    try:
        core_v1().delete_namespaced_secret(
            name=build_secret_name(build_id),
            namespace=settings.BUILD_NAMESPACE,
        )
    except ApiException:
        pass


async def reconciler_loop(stop_event: asyncio.Event) -> None:
    logger.info("build reconciler started")
    while not stop_event.is_set():
        try:
            async with async_session_maker() as session:
                res = await session.execute(
                    select(DetectorBuild).where(DetectorBuild.status.in_(IN_FLIGHT))
                )
                for b in res.scalars().all():
                    try:
                        await reconcile_build(session, b)
                    except Exception:
                        logger.exception("reconcile_build failed", extra={"build_id": str(b.id)})
        except Exception:
            logger.exception("reconciler iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
    logger.info("build reconciler stopped")
```

- [ ] **Step 3: Wire reconciler into main.py lifespan**

Modify `backend/app/main.py` — replace `lifespan`:

```python
import asyncio

from app.reconciler import reconciler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    # existing create_all + seed admin logic
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # seed admin unchanged...

    # start reconciler
    stop_event = asyncio.Event()
    reconciler_task = asyncio.create_task(reconciler_loop(stop_event))

    yield

    stop_event.set()
    await reconciler_task
```

Skip reconciler startup in tests (set env `RECONCILER_ENABLED=false` or detect test mode).

Add to `config.py`:

```python
    RECONCILER_ENABLED: bool = True
```

Gate the create_task call behind this flag.

- [ ] **Step 4: Run reconciler tests**

```bash
cd backend && uv run pytest tests/test_reconciler.py -v
```

Expected: 1 test passes (others marked as `...` — flesh out minimally to pass).

- [ ] **Step 5: Run full backend test suite**

```bash
cd backend && uv run pytest
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/reconciler.py backend/app/main.py backend/app/config.py backend/tests/test_reconciler.py
git commit -m "feat(backend): add build reconciler loop with state machine"
```

---

## Task 12: Build Helper Image (maldet_validator)

**Files:**

- Create: `charts/lolday/helpers/build-helper/Dockerfile`
- Create: `charts/lolday/helpers/build-helper/maldet_validator.py`

- [ ] **Step 1: Create Dockerfile**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv httpx

WORKDIR /app
COPY maldet_validator.py .

USER 1000
ENTRYPOINT ["python", "-m"]
CMD ["maldet_validator"]
```

- [ ] **Step 2: Create `maldet_validator.py`**

```python
"""Runtime validator: installs a detector repo, imports its BaseDetector subclass,
extracts the Pydantic JSON schema, POSTs it back to the lolday backend, and exits
with a structured error code on failure."""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx


class ValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def main() -> int:
    repo = Path(sys.argv[1])
    if not repo.is_dir():
        return _fail("repo_missing", f"not a directory: {repo}")

    try:
        _pip_install(repo)
        cls = _discover_detector_class(repo)
        schema = cls.config_class.model_json_schema()
        git_sha = _read_git_sha(repo.parent / "git-sha")
        _post_schema(schema, git_sha)
        print(f"VALIDATION OK: {cls.__module__}.{cls.__name__}", flush=True)
        return 0
    except ValidationError as e:
        return _fail(e.code, e.message)
    except Exception as e:
        return _fail("validation_error", f"{type(e).__name__}: {e}")


def _pip_install(repo: Path) -> None:
    proc = subprocess.run(
        ["uv", "pip", "install", "--system", "--no-cache-dir", str(repo), "islab-malware-detector"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ValidationError("pip_install_failed", proc.stderr[-500:])


def _discover_detector_class(repo: Path):
    """Find the first module-level subclass of BaseDetector by importing
    candidate packages from the repo."""
    from maldet import BaseDetector

    # Heuristic: import top-level packages declared in pyproject.toml or directory names
    candidates = [p.name for p in repo.iterdir() if p.is_dir() and (p / "__init__.py").is_file()]
    for pkg in candidates:
        try:
            mod = importlib.import_module(pkg)
        except Exception:
            continue
        for name in dir(mod):
            obj = getattr(mod, name)
            if isinstance(obj, type) and issubclass(obj, BaseDetector) and obj is not BaseDetector:
                return obj
    raise ValidationError(
        "missing_base_detector",
        "no BaseDetector subclass found in repo modules",
    )


def _post_schema(schema: dict, git_sha: str) -> None:
    build_id = os.environ["BUILD_ID"]
    token = os.environ["BUILD_TOKEN"]
    url = os.environ["BACKEND_URL"] + f"/api/v1/internal/builds/{build_id}/schema"
    try:
        resp = httpx.post(
            url,
            json={"schema": schema, "git_sha": git_sha},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ValidationError("schema_post_failed", str(e))


def _read_git_sha(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text().strip()


def _fail(code: str, message: str) -> int:
    payload = {"validation_error": {"code": code, "message": message}}
    print(json.dumps(payload), flush=True, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Build and push image (requires Harbor — deferred to Task 17)**

For now, just commit the source. Image will be built after Harbor is deployed in Task 17.

- [ ] **Step 4: Commit**

```bash
git add charts/lolday/helpers
git commit -m "feat(chart): add build-helper image source (runtime validator)"
```

---

## Task 13: Helm Chart — Add Harbor Dependency + Values

**Files:**

- Modify: `charts/lolday/Chart.yaml`
- Modify: `charts/lolday/values.yaml`

- [ ] **Step 1: Add Harbor dependency**

Edit `charts/lolday/Chart.yaml`, add `dependencies`:

```yaml
apiVersion: v2
name: lolday
description: Lolday ML platform
version: 0.3.0
appVersion: "0.3.0"
dependencies:
  - name: harbor
    version: "1.16.1"
    repository: https://helm.goharbor.io
    condition: harbor.enabled
```

- [ ] **Step 2: Run `helm dependency build`**

```bash
cd charts/lolday && helm repo add harbor https://helm.goharbor.io && helm repo update && helm dependency build
```

Expected: `charts/lolday/charts/harbor-1.16.1.tgz` downloaded.

- [ ] **Step 3: Add Harbor values**

Edit `charts/lolday/values.yaml`, append:

```yaml
# =============================================================================
# Harbor Container Registry
# =============================================================================
harbor:
  enabled: true
  expose:
    type: clusterIP
    tls:
      enabled: false
  externalURL: http://harbor.harbor.svc.cluster.local:80
  harborAdminPassword: "" # --set at deploy time
  persistence:
    enabled: true
    persistentVolumeClaim:
      registry:
        size: 100Gi
      jobservice:
        size: 2Gi
      database:
        size: 5Gi
      redis:
        size: 2Gi
      trivy:
        size: 10Gi
  trivy:
    enabled: true
    skipUpdate: false
  notary:
    enabled: false
  chartmuseum:
    enabled: false
  resources:
    core:
      requests: { cpu: 100m, memory: 256Mi }
      limits: { cpu: 1, memory: 1Gi }
    jobservice:
      requests: { cpu: 100m, memory: 256Mi }
      limits: { cpu: 1, memory: 1Gi }
    registry:
      requests: { cpu: 100m, memory: 256Mi }
      limits: { cpu: 1, memory: 2Gi }
    portal:
      requests: { cpu: 50m, memory: 64Mi }
      limits: { cpu: 500m, memory: 256Mi }
    database:
      requests: { cpu: 100m, memory: 256Mi }
      limits: { cpu: 1, memory: 1Gi }
    redis:
      requests: { cpu: 50m, memory: 64Mi }
      limits: { cpu: 500m, memory: 256Mi }
    trivy:
      requests: { cpu: 100m, memory: 256Mi }
      limits: { cpu: 1, memory: 1Gi }

# =============================================================================
# Backend updates for Phase 3
# =============================================================================
backend:
  # existing
  fernetKey: "" # --set
  harborAdminPassword: "" # --set (shared with harbor section for backend to use admin API)
  env:
    DOCS_ENABLED: "true"
    HARBOR_URL: "http://harbor.harbor.svc.cluster.local:80"
    HARBOR_ADMIN_USERNAME: "admin"
    HARBOR_IMAGE_PREFIX: "harbor.harbor.svc:80"
    GITHUB_API_URL: "https://api.github.com"
    BUILD_NAMESPACE: "lolday"
    BUILD_IMAGE_HELPER: "harbor.harbor.svc:80/lolday/build-helper:v1"
    BUILD_IMAGE_KANIKO: "gcr.io/kaniko-project/executor:latest"
    BUILD_IMAGE_GIT: "alpine/git:2.45"
    BUILD_TIMEOUT_SECONDS: "1200"
    BUILD_CONCURRENCY_PER_USER: "2"
    BACKEND_INTERNAL_URL: "http://backend.lolday.svc:8000"

# =============================================================================
# Registry (Phase 2, now disabled by default — Harbor replaces it)
# =============================================================================
registry:
  enabled: false
```

- [ ] **Step 4: Add Harbor namespace to umbrella chart**

Harbor installs in its own namespace. Since Helm subcharts don't create namespaces automatically, ensure `scripts/deploy.sh` creates it (Task 15).

For now verify the chart templates:

```bash
cd charts/lolday && helm template lolday . --set harbor.harborAdminPassword=x --set backend.fernetKey=y --set backend.harborAdminPassword=x --set postgresql.auth.password=z | head -50
```

Expected: no template errors. Harbor resources should appear.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/Chart.yaml charts/lolday/values.yaml charts/lolday/Chart.lock charts/lolday/charts
git commit -m "feat(chart): add Harbor sub-chart dependency and values"
```

---

## Task 14: Helm Templates — RBAC, Secrets, NetworkPolicy

**Files:**

- Create: `charts/lolday/templates/backend-rbac.yaml`
- Create: `charts/lolday/templates/backend-fernet-secret.yaml`
- Create: `charts/lolday/templates/harbor-admin-secret.yaml`
- Create: `charts/lolday/templates/build-networkpolicy.yaml`
- Modify: `charts/lolday/templates/backend.yaml` (use SA + env)
- Modify: `charts/lolday/templates/registry.yaml` (guard under flag)

- [ ] **Step 1: ServiceAccount + Role + RoleBinding**

Create `charts/lolday/templates/backend-rbac.yaml`:

```yaml
{{- if .Values.backend.enabled }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: backend
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: backend
    {{- include "lolday.labels" . | nindent 4 }}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: backend
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
rules:
  - apiGroups: [""]
    resources: [pods, pods/log]
    verbs: [get, list, watch]
  - apiGroups: [""]
    resources: [secrets, configmaps]
    verbs: [get, list, create, delete]
  - apiGroups: [batch]
    resources: [jobs]
    verbs: [get, list, create, delete, watch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: backend
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
subjects:
  - kind: ServiceAccount
    name: backend
    namespace: {{ .Values.global.namespace }}
roleRef:
  kind: Role
  name: backend
  apiGroup: rbac.authorization.k8s.io
{{- end }}
```

- [ ] **Step 2: Fernet key Secret**

Create `charts/lolday/templates/backend-fernet-secret.yaml`:

```yaml
{{- if .Values.backend.enabled }}
apiVersion: v1
kind: Secret
metadata:
  name: backend-fernet-key
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
type: Opaque
stringData:
  key: {{ required "backend.fernetKey is required" .Values.backend.fernetKey | quote }}
{{- end }}
```

- [ ] **Step 3: Harbor admin Secret (so backend can call Harbor API)**

Create `charts/lolday/templates/harbor-admin-secret.yaml`:

```yaml
{{- if .Values.backend.enabled }}
apiVersion: v1
kind: Secret
metadata:
  name: backend-harbor-admin
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
type: Opaque
stringData:
  password: {{ required "backend.harborAdminPassword is required" .Values.backend.harborAdminPassword | quote }}
{{- end }}
```

- [ ] **Step 4: Build NetworkPolicy**

Create `charts/lolday/templates/build-networkpolicy.yaml`:

```yaml
{{- if .Values.backend.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: lolday-build-egress
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app: lolday-build
  policyTypes: [Egress]
  egress:
    # DNS
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports: [{ protocol: UDP, port: 53 }]
    # Harbor
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: harbor
    # Backend (for validate container schema callback)
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.global.namespace }}
          podSelector:
            matchLabels:
              app.kubernetes.io/component: backend
      ports: [{ protocol: TCP, port: 8000 }]
    # Internet, excluding cluster internal ranges
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.42.0.0/16      # Flannel pod CIDR (default)
              - 10.43.0.0/16      # Service CIDR (default)
              - 192.168.0.0/16
              - 172.16.0.0/12
              - 169.254.0.0/16
{{- end }}
```

- [ ] **Step 5: Update backend.yaml to use SA + env + Secrets**

Modify `charts/lolday/templates/backend.yaml` — in the Deployment spec, add:

```yaml
    spec:
      serviceAccountName: backend      # NEW
      containers:
        - name: backend
          # existing image / ports ...
          env:
            # existing Phase 2 env
            - name: FERNET_KEY
              valueFrom:
                secretKeyRef:
                  name: backend-fernet-key
                  key: key
            - name: HARBOR_ADMIN_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: backend-harbor-admin
                  key: password
            # Phase 3 static env from values.yaml
            {{- range $k, $v := .Values.backend.env }}
            - name: {{ $k }}
              value: {{ $v | quote }}
            {{- end }}
```

- [ ] **Step 6: Guard registry.yaml**

Modify `charts/lolday/templates/registry.yaml`, wrap entire file:

```yaml
{{- if .Values.registry.enabled }}
# existing contents
{{- end }}
```

(If already wrapped, skip.)

- [ ] **Step 7: Render and verify**

```bash
cd charts/lolday && helm template lolday . \
  --set harbor.harborAdminPassword=x \
  --set backend.fernetKey=ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg= \
  --set backend.harborAdminPassword=x \
  --set postgresql.auth.password=z \
  | grep -E "^kind:" | sort | uniq -c
```

Expected: ServiceAccount, Role, RoleBinding, Secret (fernet, harbor-admin), NetworkPolicy all present.

- [ ] **Step 8: Commit**

```bash
git add charts/lolday/templates
git commit -m "feat(chart): add backend RBAC, secrets, and build NetworkPolicy"
```

---

## Task 15: Deployment Scripts — patch-k3s-registries.sh + deploy.sh

**Files:**

- Create: `scripts/patch-k3s-registries.sh`
- Modify: `scripts/deploy.sh`

- [ ] **Step 1: Write patch-k3s-registries.sh**

Create `scripts/patch-k3s-registries.sh`:

```bash
#!/usr/bin/env bash
# Idempotently add Harbor mirror to K3s containerd registries.yaml.
# Must be run with sudo.
#
# Safety guarantees (SSH critical):
#   1. Backup current file → .bak.<timestamp>
#   2. Read Harbor Service ClusterIP dynamically
#   3. Dry-run diff; prompt user to confirm
#   4. Write new file, restart k3s
#   5. Verify k3s is-active; rollback on failure
set -euo pipefail

FILE=/etc/rancher/k3s/registries.yaml
NAMESPACE=harbor
SERVICE=harbor

if [[ $EUID -ne 0 ]]; then
  echo "This script requires sudo." >&2
  exit 1
fi

# 1. Read Harbor ClusterIP as the caller's non-root user to reuse their kubeconfig
CLUSTER_IP=$(sudo -u "${SUDO_USER:-$(whoami)}" \
  kubectl get svc -n "$NAMESPACE" "$SERVICE" -o jsonpath='{.spec.clusterIP}')
if [[ -z "$CLUSTER_IP" ]]; then
  echo "Failed to read Harbor ClusterIP (svc $SERVICE in ns $NAMESPACE). Is Harbor deployed?" >&2
  exit 1
fi
echo "Detected Harbor ClusterIP: $CLUSTER_IP"

TS=$(date +%Y%m%d-%H%M%S)
BACKUP="${FILE}.bak.${TS}"

# 2. Backup
if [[ -f "$FILE" ]]; then
  cp -v "$FILE" "$BACKUP"
else
  install -m 0600 -o root -g root /dev/null "$FILE"
  echo "# Managed by lolday/patch-k3s-registries.sh" > "$FILE"
  echo "mirrors: {}" >> "$FILE"
fi

# 3. Compose new content (preserve any existing non-harbor entries)
NEW=$(mktemp)
python3 - "$FILE" "$CLUSTER_IP" <<'PY' > "$NEW"
import sys, yaml
path, cluster_ip = sys.argv[1], sys.argv[2]
with open(path) as f:
    data = yaml.safe_load(f) or {}
mirrors = data.setdefault("mirrors", {})
mirrors["harbor.harbor.svc:80"] = {
    "endpoint": [f"http://{cluster_ip}:80"],
}
print(yaml.safe_dump(data, sort_keys=True))
PY

# 4. Diff
echo "--- proposed changes ---"
diff -u "$FILE" "$NEW" || true
echo "------------------------"
read -r -p "Apply? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; rm "$NEW"; exit 0; }

mv "$NEW" "$FILE"
chmod 0600 "$FILE"

# 5. Restart k3s, verify
systemctl restart k3s
sleep 5
if ! systemctl is-active --quiet k3s; then
  echo "k3s FAILED to start; rolling back from $BACKUP" >&2
  cp "$BACKUP" "$FILE"
  systemctl restart k3s
  sleep 3
  systemctl is-active --quiet k3s && \
    echo "rollback OK; exiting with failure" >&2 && exit 2 || \
    (echo "CRITICAL: k3s still not active after rollback; investigate immediately" >&2 && exit 3)
fi
echo "k3s restarted successfully. Backup kept at: $BACKUP"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/patch-k3s-registries.sh
```

- [ ] **Step 3: Update deploy.sh**

Modify `scripts/deploy.sh` — ensure these steps exist:

```bash
#!/usr/bin/env bash
set -euo pipefail

# existing setup ...

# Harbor repo
helm repo add harbor https://helm.goharbor.io 2>/dev/null || true
helm repo update

# Dependencies for umbrella chart
cd charts/lolday && helm dependency build && cd -

# Values (adapt to existing patterns)
: "${HARBOR_ADMIN_PASSWORD:?HARBOR_ADMIN_PASSWORD must be set}"
: "${FERNET_KEY:?FERNET_KEY must be set — generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
: "${JWT_SECRET:?JWT_SECRET must be set}"
: "${ADMIN_EMAIL:?ADMIN_EMAIL must be set}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"

# Ensure harbor namespace
kubectl create namespace harbor --dry-run=client -o yaml | kubectl apply -f -

# Deploy
helm upgrade --install lolday ./charts/lolday \
  --namespace lolday --create-namespace \
  --set harbor.harborAdminPassword="$HARBOR_ADMIN_PASSWORD" \
  --set backend.fernetKey="$FERNET_KEY" \
  --set backend.harborAdminPassword="$HARBOR_ADMIN_PASSWORD" \
  --set postgresql.auth.password="$POSTGRES_PASSWORD" \
  --set backend.jwtSecret="$JWT_SECRET" \
  --set backend.firstAdmin.email="$ADMIN_EMAIL" \
  --set backend.firstAdmin.password="$ADMIN_PASSWORD" \
  --wait --timeout 10m

cat <<EOF

=========================================================================
  Deployment complete.

  NEXT MANUAL STEP (requires sudo):

    sudo bash scripts/patch-k3s-registries.sh

  This configures K3s containerd to resolve 'harbor.harbor.svc:80' as
  the in-cluster Harbor. The script is safe (backs up, verifies k3s
  restart succeeds, auto-rollback on failure).

=========================================================================
EOF
```

- [ ] **Step 4: Commit**

```bash
git add scripts/patch-k3s-registries.sh scripts/deploy.sh
git commit -m "feat(scripts): add patch-k3s-registries.sh and update deploy.sh for Harbor"
```

---

## Task 16: Harbor Post-install Init in Backend Lifespan

**Files:**

- Modify: `backend/app/main.py`
- Create: `backend/app/services/harbor_init.py`

- [ ] **Step 1: Create harbor_init.py**

Create `backend/app/services/harbor_init.py`:

```python
import base64
import json
import logging

from kubernetes.client import ApiException, V1ObjectMeta, V1Secret

from app.config import settings
from app.services.harbor import HarborClient
from app.services.k8s import core_v1

logger = logging.getLogger(__name__)


async def init_harbor() -> None:
    """Idempotent initialization: projects + robot account + retention + Docker config Secret.

    Safe to run on every backend startup.
    """
    if not settings.HARBOR_ADMIN_PASSWORD:
        logger.warning("HARBOR_ADMIN_PASSWORD not set — skipping Harbor init")
        return
    client = HarborClient(
        settings.HARBOR_URL,
        settings.HARBOR_ADMIN_USERNAME,
        settings.HARBOR_ADMIN_PASSWORD,
    )

    for project in ("detectors", "detectors-cache", "lolday"):
        try:
            await client.ensure_project(project, public=True)
        except Exception:
            logger.exception("ensure_project failed for %s", project)

    try:
        robot = await client.ensure_robot_account(
            "build-pusher",
            projects=["detectors", "detectors-cache", "lolday"],
        )
        if "secret" in robot:
            # Fresh robot created — persist docker config Secret
            _write_docker_config_secret(robot["name"], robot["secret"])
    except Exception:
        logger.exception("ensure_robot_account failed")

    for project in ("detectors", "detectors-cache"):
        try:
            keep = 3 if project == "detectors" else 0  # cache uses TTL not count
            if keep > 0:
                await client.set_retention_policy(project, keep_n_recent=keep)
        except Exception:
            logger.exception("set_retention_policy failed for %s", project)


def _write_docker_config_secret(robot_name: str, robot_secret: str) -> None:
    registry = settings.HARBOR_IMAGE_PREFIX
    cfg = {
        "auths": {
            registry: {
                "auth": base64.b64encode(f"{robot_name}:{robot_secret}".encode()).decode()
            }
        }
    }
    body = V1Secret(
        metadata=V1ObjectMeta(name="harbor-push-cred", namespace=settings.BUILD_NAMESPACE),
        type="kubernetes.io/dockerconfigjson",
        string_data={".dockerconfigjson": json.dumps(cfg)},
    )
    try:
        core_v1().replace_namespaced_secret(
            name="harbor-push-cred",
            namespace=settings.BUILD_NAMESPACE,
            body=body,
        )
    except ApiException as e:
        if e.status == 404:
            core_v1().create_namespaced_secret(
                namespace=settings.BUILD_NAMESPACE, body=body
            )
        else:
            raise
```

- [ ] **Step 2: Wire into lifespan**

Modify `backend/app/main.py` inside `lifespan`:

```python
from app.services.harbor_init import init_harbor

# after create_all / seed_admin, before reconciler task:
try:
    await init_harbor()
except Exception:
    logger.exception("harbor init failed — continuing, reconciler may not be effective")
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/harbor_init.py backend/app/main.py
git commit -m "feat(backend): init Harbor projects/robot/retention on startup"
```

---

## Task 17: Build + Push Backend Image and build-helper Image to Harbor

**Files:** (no new files; operational steps)

- [ ] **Step 1: Deploy the chart first time**

On server30:

```bash
# Generate secrets
export FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export HARBOR_ADMIN_PASSWORD="$(openssl rand -base64 24)"
export POSTGRES_PASSWORD="$(openssl rand -base64 24)"
export JWT_SECRET="$(openssl rand -base64 48)"
export ADMIN_EMAIL="admin@lolday.dev"
export ADMIN_PASSWORD="$(openssl rand -base64 18)"

# Save these — you'll need HARBOR_ADMIN_PASSWORD to log in to Harbor UI later
echo "Harbor admin password: $HARBOR_ADMIN_PASSWORD"
echo "First app admin email: $ADMIN_EMAIL / password: $ADMIN_PASSWORD"

# Run deploy
bash scripts/deploy.sh
```

Expected: Harbor + backend + PostgreSQL + Redis up. Backend logs contain "seed admin" + "harbor init" lines.

- [ ] **Step 2: Run patch-k3s-registries.sh**

```bash
sudo bash scripts/patch-k3s-registries.sh
```

Answer `y` at diff prompt. k3s restarts. Verify:

```bash
sudo systemctl is-active k3s     # active
kubectl get pods -A              # all running
```

- [ ] **Step 3: Build + push backend image to Harbor**

On server30:

```bash
cd backend
docker build -t harbor.harbor.svc:80/lolday/lolday-backend:phase3 .

# Port-forward Harbor for push from dev workstation (or use hostPort via patch)
kubectl port-forward -n harbor svc/harbor 8080:80 &
docker tag harbor.harbor.svc:80/lolday/lolday-backend:phase3 \
           localhost:8080/lolday/lolday-backend:phase3
docker login localhost:8080 -u admin -p "$HARBOR_ADMIN_PASSWORD"
docker push localhost:8080/lolday/lolday-backend:phase3
kill %1  # stop port-forward
```

- [ ] **Step 4: Build + push build-helper image**

```bash
cd charts/lolday/helpers/build-helper
docker build -t harbor.harbor.svc:80/lolday/build-helper:v1 .
kubectl port-forward -n harbor svc/harbor 8080:80 &
docker tag harbor.harbor.svc:80/lolday/build-helper:v1 localhost:8080/lolday/build-helper:v1
docker push localhost:8080/lolday/build-helper:v1
kill %1
```

- [ ] **Step 5: Update backend Deployment to use new image**

```bash
helm upgrade lolday ./charts/lolday \
  -n lolday \
  --reuse-values \
  --set backend.image=harbor.harbor.svc:80/lolday/lolday-backend:phase3
```

Expected: backend Pod restarts with new image. `kubectl logs -n lolday deployment/backend` shows reconciler started.

- [ ] **Step 6: Disable registry:2 if it was enabled previously**

Already disabled via `registry.enabled: false` in values.yaml.

Clean up PVC if desired (only after confirming no rollback needed):

```bash
kubectl delete pvc -n lolday registry-data   # DESTRUCTIVE — confirm first
```

- [ ] **Step 7: Commit any config tweaks**

```bash
git status
# if values / scripts changed during troubleshooting, commit here
```

---

## Task 18: E2E Smoke Test with upxelfdet

**Files:**

- Create: `docs/phase3-e2e-checklist.md`

- [ ] **Step 1: Register the platform admin and create developer test user**

```bash
kubectl port-forward -n lolday svc/backend 8000:8000 &
export TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/jwt/login \
  -d "username=$ADMIN_EMAIL&password=$ADMIN_PASSWORD" \
  -H "Content-Type: application/x-www-form-urlencoded" | jq -r .access_token)

curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "dev@lolday.dev", "password": "DevPass123!"}'

# promote to developer
DEV_ID=$(curl -s http://localhost:8000/api/v1/admin/users -H "Authorization: Bearer $TOKEN" \
  | jq -r '.items[] | select(.email=="dev@lolday.dev") | .id')
curl -X PATCH http://localhost:8000/api/v1/users/$DEV_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "developer"}'
```

- [ ] **Step 2: Log in as developer, set PAT**

```bash
export DEV_TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/jwt/login \
  -d "username=dev@lolday.dev&password=DevPass123!" \
  -H "Content-Type: application/x-www-form-urlencoded" | jq -r .access_token)

curl -X PUT http://localhost:8000/api/v1/users/me/git-credential \
  -H "Authorization: Bearer $DEV_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"provider\": \"github\", \"token\": \"$GITHUB_PAT\"}"
```

(Replace `$GITHUB_PAT` with a real token. The target repo `upxelfdet` is public so a minimal-scope token is fine.)

- [ ] **Step 3: Register upxelfdet**

```bash
curl -X POST http://localhost:8000/api/v1/detectors \
  -H "Authorization: Bearer $DEV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"git_url": "https://github.com/bolin8017/upxelfdet"}'
# capture returned detector id
```

- [ ] **Step 4: List available tags**

```bash
curl http://localhost:8000/api/v1/detectors/$DETECTOR_ID/available-tags \
  -H "Authorization: Bearer $DEV_TOKEN"
```

Expected: JSON list of upxelfdet tags from GitHub.

- [ ] **Step 5: Trigger a build**

```bash
BUILD=$(curl -s -X POST http://localhost:8000/api/v1/detectors/$DETECTOR_ID/builds \
  -H "Authorization: Bearer $DEV_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"git_tag": "v0.1.0"}')
echo $BUILD | jq
BUILD_ID=$(echo $BUILD | jq -r .id)
```

Expected: status `cloning` or `pending`.

- [ ] **Step 6: Observe progress**

```bash
watch -n 2 "curl -s http://localhost:8000/api/v1/detectors/$DETECTOR_ID/builds/$BUILD_ID \
  -H 'Authorization: Bearer $DEV_TOKEN' | jq '.status, .failure_reason'"
```

Expected progression: cloning → validating → building → scanning → succeeded (approximately 5-10 minutes).

- [ ] **Step 7: Verify version recorded**

```bash
curl http://localhost:8000/api/v1/detectors/$DETECTOR_ID/versions \
  -H "Authorization: Bearer $DEV_TOKEN" | jq
```

Expected: one version with tag `v0.1.0`, harbor_image set, image_digest non-empty.

- [ ] **Step 8: Verify image actually exists in Harbor**

```bash
kubectl port-forward -n harbor svc/harbor 8080:80 &
# Browse: http://localhost:8080 (admin / $HARBOR_ADMIN_PASSWORD)
# or API:
curl -u "admin:$HARBOR_ADMIN_PASSWORD" \
  http://localhost:8080/api/v2.0/projects/detectors/repositories/upxelfdet/artifacts | jq
kill %1
```

Expected: artifact with tag `v0.1.0`, scan_overview showing `Success`, 0 Critical / 0 High.

- [ ] **Step 9: Write checklist document**

Create `docs/phase3-e2e-checklist.md` with the above command blocks, plus:

- Troubleshooting (common failures and remediation)
- Secret rotation procedure (Fernet key)
- Harbor storage monitoring tip: `kubectl exec -n harbor harbor-core-xxx -- df -h /storage`

- [ ] **Step 10: Commit**

```bash
git add docs/phase3-e2e-checklist.md
git commit -m "docs: add Phase 3 E2E smoke test checklist"
```

---

## Final: Merge to main

- [ ] **Step 1: Run full test suite one more time**

```bash
cd backend && uv run pytest -v
```

Expected: all Phase 2 + Phase 3 tests pass.

- [ ] **Step 2: Compare to main**

```bash
git fetch origin
git log --oneline origin/main..HEAD
```

- [ ] **Step 3: Push dev branch and open PR**

```bash
git push origin dev
gh pr create --base main --head dev \
  --title "Phase 3: Detector Lifecycle" \
  --body-file - <<'EOF'
## Summary

Delivers detector registration, sandboxed Kaniko build pipeline, Harbor
with bundled Trivy CVE scanning, version management, and Pydantic config
schema storage. Replaces Phase 2's registry:2 with Harbor.

## Test plan

- [x] All Phase 2 regression tests pass
- [x] Unit tests for services/crypto, git, validator, harbor, build,
      reconciler
- [x] Integration tests for detector/build/credential/internal routers
- [x] E2E smoke test: register upxelfdet → build v0.1.0 → verify in
      Harbor (see docs/phase3-e2e-checklist.md)
- [x] patch-k3s-registries.sh dry-run + restart verified safe
- [x] SSH port 9453 remains operational throughout deployment

## Decisions

See `docs/superpowers/specs/2026-04-14-phase3-detector-lifecycle-design.md`
§Decisions & Amendments for key choices (drop Cilium, HTTP internal,
JSON schema deferred, K8s Job vs Celery).
EOF
```

- [ ] **Step 4: Squash merge to main**

After review, use the GitHub UI or `gh pr merge --squash`.

---

## Phase 3 Definition of Done

- [ ] All unit + integration tests pass in CI (or local `uv run pytest`)
- [ ] `helm template` renders without errors
- [ ] Full E2E smoke test passes against upxelfdet
- [ ] Harbor shows `detectors/upxelfdet:v0.1.0` with green Trivy scan
- [ ] SSH (port 9453) operational after every deploy step
- [ ] `docs/superpowers/specs/2026-04-14-phase3-detector-lifecycle-design.md` unchanged (no scope creep)
- [ ] This plan committed alongside the spec; PR merged to main
