import os

os.environ.setdefault(
    "FERNET_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
)
os.environ.setdefault("RECONCILER_ENABLED", "false")
os.environ.setdefault("COOKIE_SECURE", "false")
# Point SAMPLES_LOCAL_ROOT to a non-existent dir so dataset integrity spot-checks
# skip during tests. Production uses /data/{malware,benign}-samples.
os.environ.setdefault("SAMPLES_LOCAL_ROOT", "/nonexistent-samples-root-for-tests")

import pytest
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


@pytest_asyncio.fixture
async def auth_client_user(client):
    """Authenticated AsyncClient for a 'user'-role user."""
    await register_user(client, "user@example.dev", "Password123!")
    token = await login_user(client, "user@example.dev", "Password123!")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest_asyncio.fixture
async def auth_client_developer(client):
    """AsyncClient authenticated as a developer-role user.

    Uses direct DB update to set role (seed admin isn't set up in tests).
    """
    await register_user(client, "dev@example.dev", "DevPass123!")
    token = await login_user(client, "dev@example.dev", "DevPass123!")
    # Promote to developer via direct DB update
    from sqlalchemy import update
    from app.models import Role, User
    async with test_session_maker() as session:
        await session.execute(
            update(User)
            .where(User.email == "dev@example.dev")
            .values(role=Role.DEVELOPER)
        )
        await session.commit()
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest_asyncio.fixture
async def auth_client_admin(client):
    """AsyncClient authenticated as an admin-role user."""
    await register_user(client, "adm@example.dev", "AdmPass123!")
    token = await login_user(client, "adm@example.dev", "AdmPass123!")
    from sqlalchemy import update
    from app.models import Role, User
    async with test_session_maker() as session:
        await session.execute(
            update(User)
            .where(User.email == "adm@example.dev")
            .values(role=Role.ADMIN, is_superuser=True)
        )
        await session.commit()
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest_asyncio.fixture
async def user_client(client):
    """Authenticated AsyncClient for a regular user (alias used by dataset tests)."""
    await register_user(client, "user1@example.dev", "Password123!")
    token = await login_user(client, "user1@example.dev", "Password123!")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest_asyncio.fixture
async def second_user_client():
    """Distinct authenticated client with a different user from `user_client`.

    Creates its own AsyncClient so it doesn't share headers with user_client.
    Uses the same test DB (same test_session_maker / test_engine).
    """
    from app.main import app

    async def override():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await register_user(c, "user2@example.dev", "Password123!")
        token = await login_user(c, "user2@example.dev", "Password123!")
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


@pytest_asyncio.fixture
async def db_session():
    async with test_session_maker() as session:
        yield session


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


@pytest.fixture(autouse=True)
def mock_mlflow(request, monkeypatch):
    if "no_mock_mlflow" in request.keywords:
        # Restore experiments_proxy.MlflowClient to the real class in case a
        # previous test's mock_mlflow stub leaked into the module binding.
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

    # Ensure app modules are imported before patching so we can restore correctly.
    import app.services.mlflow_client as mc
    import app.routers.jobs as jobs_mod
    import app.routers.models_registry as mr_mod
    import app.routers.experiments_proxy as ep_mod

    real_mlflow_cls = mc.MlflowClient  # capture original before any patching

    stub = _Stub()
    monkeypatch.setattr(mc, "MlflowClient", lambda *a, **kw: stub)
    # Also patch where it's imported in routers
    monkeypatch.setattr(jobs_mod, "MlflowClient", lambda *a, **kw: stub)
    monkeypatch.setattr(mr_mod, "MlflowClient", lambda *a, **kw: stub)
    monkeypatch.setattr(ep_mod, "MlflowClient", lambda *a, **kw: stub)
    yield
    # Restore ep_mod explicitly in case monkeypatch didn't capture it before first import
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
    # Register a PAT so build can proceed
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

    # Promote seed_user to DEVELOPER so transition rules allow owner transitions
    async with test_session_maker() as _s:
        await _s.execute(
            sa_update(User)
            .where(User.id == seed_user.id)
            .values(role=Role.DEVELOPER)
        )
        await _s.commit()

    async def _seed(name: str = "upxelfdet"):
        # Use a unique detector name per call to avoid UNIQUE constraint on
        # (owner_id, git_url) and name when _seed is called multiple times.
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
