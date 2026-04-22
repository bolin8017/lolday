import os

os.environ.setdefault(
    "FERNET_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
)
os.environ.setdefault("RECONCILER_ENABLED", "false")
os.environ.setdefault("SAMPLES_LOCAL_ROOT", "/nonexistent-samples-root-for-tests")
# Phase 10.2: opt out of production SSO validation — tests use dependency_override
# to inject fake users, so CF_ACCESS_* can stay blank.
os.environ.setdefault("ENVIRONMENT", "test")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_async_session
from app.models import Base, Role, User

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


async def _make_user(
    email: str,
    role: Role = Role.USER,
    is_superuser: bool = False,
) -> User:
    """Insert or return a test User row with the given role."""
    async with test_session_maker() as session:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        user = User(
            email=email,
            hashed_password="!testing-only!",
            role=role,
            display_name=email.split("@", 1)[0],
            is_active=True,
            is_superuser=is_superuser,
            is_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _install_header_based_auth_override() -> None:
    """Install a cf_access_user override keyed by the X-Test-User-Email request header.

    This preserves per-client identity when tests run two clients side-by-side
    (e.g. `user_client` + `second_user_client`). The real production dependency
    parses identity out of a JWT; in tests each client just sets a test header
    pointing at a pre-seeded user row.
    """
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.auth.cf_access import cf_access_user
    from app.main import app

    async def _fake_auth(
        request: Request,
        session: AsyncSession = Depends(get_async_session),
    ) -> User:
        email = request.headers.get("x-test-user-email")
        if not email:
            raise HTTPException(401, "missing X-Test-User-Email (test fixture)")
        row = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(401, f"test fixture: user not seeded: {email}")
        return row

    app.dependency_overrides[cf_access_user] = _fake_auth


@pytest_asyncio.fixture
async def client():
    from app.main import app

    async def override():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override
    _install_header_based_auth_override()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _as_user(client: AsyncClient, email: str) -> AsyncClient:
    client.headers["x-test-user-email"] = email
    return client


@pytest_asyncio.fixture
async def auth_client_user(client):
    await _make_user("user@example.dev", role=Role.USER)
    return _as_user(client, "user@example.dev")


@pytest_asyncio.fixture
async def auth_client_developer(client):
    await _make_user("dev@example.dev", role=Role.DEVELOPER)
    return _as_user(client, "dev@example.dev")


@pytest_asyncio.fixture
async def auth_client_admin(client):
    await _make_user("adm@example.dev", role=Role.ADMIN, is_superuser=True)
    return _as_user(client, "adm@example.dev")


@pytest_asyncio.fixture
async def user_client(client):
    """Alias used by dataset tests; regular USER-role user."""
    await _make_user("user1@example.dev", role=Role.USER)
    return _as_user(client, "user1@example.dev")


@pytest_asyncio.fixture
async def second_user_client():
    """Distinct client with a different user so it can run alongside user_client."""
    from app.main import app

    async def override():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override
    _install_header_based_auth_override()
    await _make_user("user2@example.dev", role=Role.USER)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers["x-test-user-email"] = "user2@example.dev"
        yield c


@pytest_asyncio.fixture
async def db_session():
    async with test_session_maker() as session:
        yield session


@pytest.fixture(autouse=True)
def fake_redis_for_rate_limit(monkeypatch):
    """Autouse fakeredis so rate_limit service uses an in-memory store per test."""
    from fakeredis.aioredis import FakeRedis
    fake = FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.services.rate_limit._redis", fake)
    yield


@pytest.fixture(autouse=True)
def mock_k8s_batch(monkeypatch):
    """Autouse: replace kubernetes BatchV1Api + CoreV1Api create/delete with in-memory stubs."""
    class _StubBatch:
        def __init__(self):
            self.jobs = {}
        def create_namespaced_job(self, namespace, body, **kw):
            name = body["metadata"]["name"] if isinstance(body, dict) else body.metadata.name
            self.jobs[name] = body
            return body
        def delete_namespaced_job(self, name, namespace, **kw):
            self.jobs.pop(name, None)
        def read_namespaced_job(self, name, namespace, **kw):
            from kubernetes.client.exceptions import ApiException
            if name not in self.jobs:
                raise ApiException(status=404)
            class _S: status = type("S", (), {"succeeded": None, "failed": None})()
            return _S()
    stub = _StubBatch()
    monkeypatch.setattr("app.services.k8s.batch_v1", lambda: stub)

    class _StubCore:
        def create_namespaced_secret(self, namespace, body, **kw): return body
        def delete_namespaced_secret(self, name, namespace, **kw): pass
        def list_namespaced_pod(self, namespace, **kw):
            class _R: items = []
            return _R()
        def read_namespaced_pod_log(self, **kw): return ""
    monkeypatch.setattr("app.services.k8s.core_v1", lambda: _StubCore())

    # Phase 7.3 routed training jobs through Volcano CRDs via
    # CustomObjectsApi.create_namespaced_custom_object(). Without this stub,
    # `test_jobs` and `test_rate_limits` POST /api/v1/jobs calls leak real
    # `batch.volcano.sh/v1alpha1 Job` CRs onto whatever cluster kubectl is
    # pointed at (observed: 515 stale Pending Jobs on server30 from a single
    # dev run).
    class _StubVolcano:
        def __init__(self):
            self.objects = {}
        def create_namespaced_custom_object(self, group, version, namespace, plural, body, **kw):
            name = (body.get("metadata") or {}).get("name") if isinstance(body, dict) else body.metadata.name
            self.objects[name] = body
            return body
        def get_namespaced_custom_object(self, *a, **kw):
            from kubernetes.client.exceptions import ApiException
            raise ApiException(status=404)
        def delete_namespaced_custom_object(self, *a, **kw):
            return {}
        def list_namespaced_custom_object(self, *a, **kw):
            return {"items": list(self.objects.values())}
    monkeypatch.setattr("app.services.k8s.volcano_v1alpha1", lambda: _StubVolcano())


@pytest.fixture(autouse=True)
def mock_mlflow(request, monkeypatch):
    if "no_mock_mlflow" in request.keywords:
        import app.services.mlflow_client as mc
        import app.routers.experiments_proxy as ep_mod
        ep_mod.MlflowClient = mc.MlflowClient
        yield
        return

    class _Stub:
        exp_counter = 0
        run_counter = 0

        async def get_or_create_experiment(self, name, artifact_location=None):
            _Stub.exp_counter += 1
            return f"exp-{_Stub.exp_counter}"

        async def create_run(self, experiment_id, tags=None):
            _Stub.run_counter += 1
            return f"run-{_Stub.run_counter}"

        async def get_run(self, run_id):
            return {"info": {"status": "FINISHED", "run_id": run_id, "experiment_id": "exp-1"},
                    "data": {"metrics": {"accuracy": 0.9}, "tags": {}, "params": {}}}

        async def update_run(self, run_id, **kw): pass
        async def set_run_tag(self, *a, **kw): pass

        async def transition_model_version_stage(self, name, version, stage, archive_existing_versions=False):
            return {"name": name, "version": str(version), "current_stage": stage}

        async def delete_model_version(self, name, version):
            pass

        async def create_registered_model(self, name):
            return {"name": name}

        async def create_model_version(self, name, source, run_id):
            return {"name": name, "version": "1", "run_id": run_id}

        async def search_registered_models(self, max_results=100):
            return []

        async def search_model_versions(self, filter_string=None, max_results=200):
            return []

        async def search_experiments(self, max_results=100):
            return []

        async def search_runs(self, experiment_ids, filter_string=None, max_results=100):
            return []

    import app.services.mlflow_client as mc
    import app.routers.jobs as jobs_mod
    import app.routers.models_registry as mr_mod
    import app.routers.experiments_proxy as ep_mod

    real_mlflow_cls = mc.MlflowClient

    stub = _Stub()
    monkeypatch.setattr(mc, "MlflowClient", lambda *a, **kw: stub)
    monkeypatch.setattr(jobs_mod, "MlflowClient", lambda *a, **kw: stub)
    monkeypatch.setattr(mr_mod, "MlflowClient", lambda *a, **kw: stub)
    monkeypatch.setattr(ep_mod, "MlflowClient", lambda *a, **kw: stub)
    yield
    if ep_mod.MlflowClient is not real_mlflow_cls:
        ep_mod.MlflowClient = real_mlflow_cls


@pytest_asyncio.fixture
async def seed_user(user_client):
    """Return the User ORM object for the user behind user_client."""
    from sqlalchemy import select
    from app.models import User
    async with test_session_maker() as session:
        result = await session.execute(
            select(User).where(User.email == "user1@example.dev")
        )
        return result.scalar_one()


@pytest_asyncio.fixture
async def seed_detector_version(db_session, seed_user):
    """Return a callable that inserts a minimal DetectorVersion row."""
    async def _seed(name: str = "upxelfdet", git_tag: str = "v0.4.0"):
        from app.models import Detector, DetectorVersion
        from app.models.detector import DetectorVersionStatus
        det = Detector(
            name=name, display_name=name,
            git_url=f"https://github.com/test/{name}.git",
            owner_id=seed_user.id,
        )
        db_session.add(det)
        await db_session.flush()
        dv = DetectorVersion(
            detector_id=det.id,
            git_tag=git_tag,
            git_sha="a" * 40,
            harbor_image=f"harbor.harbor.svc:80/detectors/{name}:{git_tag}",
            image_digest="sha256:" + "a" * 64,
            config_schema={"type": "object", "properties": {"seed": {"type": "integer"}}},
            status=DetectorVersionStatus.ACTIVE,
        )
        db_session.add(dv)
        await db_session.commit()
        return str(dv.id)
    return _seed


@pytest_asyncio.fixture
async def seed_dataset(user_client):
    from pathlib import Path
    FIXTURE_CSV = (Path(__file__).parent / "fixtures" / "sample_dataset.csv").read_text()
    async def _seed(name: str = "ds"):
        r = await user_client.post(
            "/api/v1/datasets",
            json={"name": name, "csv_content": FIXTURE_CSV},
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]
    return _seed


@pytest_asyncio.fixture
async def seed_detector(auth_client_developer, monkeypatch):
    from app.routers import detectors as dr

    async def fake_meta(url, pat):
        return {"name": "upxelfdet", "description": "demo", "display_name": "upxelfdet"}

    monkeypatch.setattr(dr, "_clone_and_validate", fake_meta)
    await auth_client_developer.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_testtoken1234567890"},
    )
    create = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    return create.json()["id"]


@pytest_asyncio.fixture
async def seed_model_version(db_session, seed_user, seed_detector_version, seed_dataset):
    """Insert a ModelVersion row tied to a fresh detector_version + fake source job.

    Also promotes the seed_user to DEVELOPER so transition tests can run.
    """
    from uuid import UUID, uuid4

    from sqlalchemy import update as sa_update
    from app.models import Job, ModelVersion, Role, User
    from app.models.job import JobStatus, JobType
    from app.models.model_registry import ModelVersionStage

    async with test_session_maker() as _s:
        await _s.execute(
            sa_update(User)
            .where(User.id == seed_user.id)
            .values(role=Role.DEVELOPER)
        )
        await _s.commit()

    async def _seed(name: str = "upxelfdet"):
        unique_det_name = f"{name}-{uuid4().hex[:8]}"
        dv_id_str = await seed_detector_version(name=unique_det_name)
        ds_id_str = await seed_dataset(name=f"ds-for-{name}-{uuid4().hex[:6]}")
        job = Job(
            type=JobType.TRAIN,
            status=JobStatus.SUCCEEDED,
            detector_version_id=UUID(dv_id_str),
            train_dataset_id=UUID(ds_id_str),
            test_dataset_id=UUID(ds_id_str),
            owner_id=seed_user.id,
            resolved_config={},
            mlflow_experiment_id="42",
            mlflow_run_id=f"run-{uuid4().hex[:8]}",
            idempotency_key=uuid4().hex,
        )
        db_session.add(job)
        await db_session.flush()

        from sqlalchemy import select, func
        row = await db_session.execute(
            select(func.coalesce(func.max(ModelVersion.mlflow_version), 0)).where(
                ModelVersion.mlflow_name == name
            )
        )
        next_version = row.scalar_one() + 1

        mv = ModelVersion(
            mlflow_name=name,
            mlflow_version=next_version,
            mlflow_run_id=job.mlflow_run_id,
            current_stage=ModelVersionStage.NONE,
            detector_version_id=UUID(dv_id_str),
            source_job_id=job.id,
            owner_id=seed_user.id,
        )
        db_session.add(mv)
        await db_session.commit()
        await db_session.refresh(mv)
        return name, next_version
    return _seed
