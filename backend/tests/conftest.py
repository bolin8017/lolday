import os

from cryptography.fernet import Fernet

# H-17a: never reuse a hardcoded Fernet key in tests — the value would be
# public via git, and any operator who copies it into .lolday-secrets.env
# makes encrypted columns trivially decryptable. Per-session fresh key.
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("RECONCILER_ENABLED", "false")
os.environ.setdefault("FIFO_RECONCILER_ENABLED", "false")
os.environ.setdefault("SAMPLES_LOCAL_ROOT", "/nonexistent-samples-root-for-tests")
# Phase 10.2: opt out of production SSO validation — tests use dependency_override
# to inject fake users, so CF_ACCESS_* can stay blank.
os.environ.setdefault("ENVIRONMENT", "test")

import pytest
import pytest_asyncio
import sqlalchemy as sa
from app.db import get_async_session
from app.models import Base, Role, User
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"
test_engine = create_async_engine(TEST_DATABASE_URL)
test_session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

# SQLite disables foreign-key enforcement by default.  Enable it so that
# ondelete=CASCADE on ModelVersion.registered_model_id (and similar FKs)
# is actually enforced during tests, matching production Postgres behaviour.
from sqlalchemy import event  # noqa: E402  # must come after test_engine is created


@event.listens_for(test_engine.sync_engine, "connect")
def _set_sqlite_fk_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Disable FK enforcement for teardown so that drop_all succeeds even
    # when there are unresolvable cyclic FKs (job ↔ model_version).
    async with test_engine.begin() as conn:
        await conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(Base.metadata.drop_all)


async def _make_user(
    email: str,
    role: Role = Role.USER,
) -> User:
    """Insert or return a test User row with the given role."""
    async with test_session_maker() as session:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        from app.services.user_handle import (
            derive_handle_from_email,
            next_unique_handle,
        )
        from sqlalchemy import select as _select

        existing_handles = set(
            (await session.execute(_select(User.handle))).scalars().all()
        )
        base_handle = derive_handle_from_email(email)
        handle = next_unique_handle(base_handle, existing=existing_handles)
        user = User(
            email=email,
            handle=handle,
            role=role,
            display_name=email.split("@", 1)[0],
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
    from app.auth.cf_access import cf_access_user
    from app.main import app
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy.ext.asyncio import AsyncSession

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


@pytest_asyncio.fixture
async def internal_client():
    """AsyncClient bound to the internal sub-app (production port 8001).

    /api/v1/internal/* routes were split off ``app.main:app`` in
    M-internal-split — production now serves them via ``app.internal_app``
    on container port 8001, gated by NetworkPolicy to lolday-jobs only.
    Tests that hit ``/api/v1/internal/*`` must use this client; auth is
    via the ``Authorization: Bearer <job-token>`` header (require_job_token),
    no CF Access user override is needed.
    """
    from app.internal_app import internal_app

    async def override():
        async with test_session_maker() as session:
            yield session

    internal_app.dependency_overrides[get_async_session] = override
    transport = ASGITransport(app=internal_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    internal_app.dependency_overrides.clear()


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
    await _make_user("adm@example.dev", role=Role.ADMIN)
    return _as_user(client, "adm@example.dev")


@pytest_asyncio.fixture
async def auth_client_service_token(client):
    """CF Access service-token principal — role=SERVICE_TOKEN, synthesised
    ``service-<name>@cf-access.local`` email shape (matches what
    ``app.auth.cf_access`` mints for service-token JWTs)."""
    await _make_user("service-test@cf-access.local", role=Role.SERVICE_TOKEN)
    return _as_user(client, "service-test@cf-access.local")


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
def _mock_k8s_load_config(monkeypatch):
    """Skip loading real kubeconfig in tests.

    Production code paths reach `app.services.k8s.load_config()` via service
    helpers; tests already mock the downstream API calls. Without this no-op
    patch, tests fail in CI (no in-cluster config, no ~/.kube/config) even
    though they don't actually need cluster access.

    We replace `app.services.k8s.load_config` with a variant that swallows
    ConfigException so CI runners (no in-cluster config, no ~/.kube/config)
    don't blow up. On a local workstation with a real kubeconfig the wrapped
    function still loads it; in CI it silently does nothing, and because
    `mock_k8s_batch` already stubs the downstream API objects (batch_v1,
    core_v1, volcano_v1alpha1) no real kubernetes traffic is attempted.

    `monkeypatch.setattr` replaces the module attribute; the original
    `@lru_cache(maxsize=1)` decoration on the production `load_config`
    is irrelevant during the test because callers now reach the
    replacement function instead.
    """
    import contextlib

    from kubernetes.config.config_exception import (
        ConfigException as _KubeConfigException,
    )

    def _safe_load_config() -> None:
        from kubernetes import config as _kube_config

        try:
            _kube_config.load_incluster_config()
        except _KubeConfigException:
            with contextlib.suppress(_KubeConfigException):
                # CI: no kubeconfig available; tests mock downstream API calls
                _kube_config.load_kube_config()

    monkeypatch.setattr("app.services.k8s.load_config", _safe_load_config)


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
            name = (
                body["metadata"]["name"]
                if isinstance(body, dict)
                else body.metadata.name
            )
            self.jobs[name] = body
            return body

        def delete_namespaced_job(self, name, namespace, **kw):
            self.jobs.pop(name, None)

        def read_namespaced_job(self, name, namespace, **kw):
            from kubernetes.client.exceptions import ApiException

            if name not in self.jobs:
                raise ApiException(status=404)

            class _S:
                status = type("S", (), {"succeeded": None, "failed": None})()

            return _S()

    stub = _StubBatch()
    monkeypatch.setattr("app.services.k8s.batch_v1", lambda: stub)

    class _StubCore:
        def create_namespaced_secret(self, namespace, body, **kw):
            return body

        def delete_namespaced_secret(self, name, namespace, **kw):
            pass

        def list_namespaced_pod(self, namespace, **kw):
            class _R:
                items: list = []  # noqa: RUF012  # stub inner class, ClassVar not needed

            return _R()

        def read_namespaced_pod_log(self, **kw):
            return ""

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

        def create_namespaced_custom_object(
            self, group, version, namespace, plural, body, **kw
        ):
            name = (
                (body.get("metadata") or {}).get("name")
                if isinstance(body, dict)
                else body.metadata.name
            )
            self.objects[name] = body
            return body

        # Phase 2 — services/k8s.ensure_user_queue creates cluster-scoped
        # Volcano Queues. Idempotent stub: silently overwrites on re-create.
        def create_cluster_custom_object(self, group, version, plural, body, **kw):
            name = (
                (body.get("metadata") or {}).get("name")
                if isinstance(body, dict)
                else body.metadata.name
            )
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

    # Patch the rebound `from app.services.k8s import core_v1` (etc.) names
    # in every caller module — Python's `from ... import ...` creates a new
    # module-local binding that is NOT updated by patching the source. Locally
    # this gap was masked because the operator's real kubeconfig let the
    # un-patched calls reach a live cluster; in CI there is no cluster.
    for _mod, _names in [
        ("app.services.harbor_init", ["core_v1"]),
        ("app.services.cluster_status", ["volcano_v1alpha1"]),
        # Phase 6d: vcjob + token Secret creation moved into jobs_dispatch;
        # patch the new home so integration tests don't reach a live cluster.
        ("app.services.jobs_dispatch", ["core_v1", "volcano_v1alpha1"]),
        ("app.routers.detectors", ["batch_v1", "core_v1"]),
        ("app.routers.jobs", ["batch_v1", "core_v1"]),
        ("app.reconciler.builds", ["batch_v1", "core_v1"]),
        ("app.reconciler.jobs", ["core_v1", "volcano_v1alpha1"]),
        ("app.reconciler.log_capture", ["core_v1"]),
        ("app.reconciler.orphans", ["core_v1", "volcano_v1alpha1"]),
    ]:
        for _name in _names:
            if _name == "batch_v1":
                monkeypatch.setattr(f"{_mod}.{_name}", lambda: stub)
            elif _name == "core_v1":
                monkeypatch.setattr(f"{_mod}.{_name}", lambda: _StubCore())
            else:
                monkeypatch.setattr(f"{_mod}.{_name}", lambda: _StubVolcano())


@pytest.fixture(autouse=True)
def mock_mlflow(request, monkeypatch):
    if "no_mock_mlflow" in request.keywords:
        import app.routers.experiments_proxy as ep_mod
        import app.services.mlflow_client as mc

        ep_mod.MlflowClient = mc.MlflowClient
        yield
        return

    class _Stub:
        exp_counter = 0
        run_counter = 0

        def __init__(self) -> None:
            self.rename_calls: list[tuple[str, str]] = []
            self.deleted_registered_models: list[str] = []
            self.create_registered_model_calls: list[str] = []
            self._mv_version_counter: int = 0
            self.experiment_creates: list[str] = []
            self.runs_created: list[tuple[str, list[dict[str, str]]]] = []
            self.run_tags_set: list[tuple[str, str, str]] = []

        async def get_or_create_experiment(self, name, artifact_location=None):
            _Stub.exp_counter += 1
            self.experiment_creates.append(name)
            return f"exp-{_Stub.exp_counter}"

        async def create_run(self, experiment_id, *, start_time_ms, tags=None):
            _Stub.run_counter += 1
            self.runs_created.append((experiment_id, list(tags or [])))
            return f"run-{_Stub.run_counter}"

        async def set_experiment_tag(self, experiment_id, key, value):
            self.run_tags_set.append((experiment_id, key, value))

        async def get_run(self, run_id):
            return {
                "info": {
                    "status": "FINISHED",
                    "run_id": run_id,
                    "experiment_id": "exp-1",
                },
                "data": {"metrics": {"accuracy": 0.9}, "tags": {}, "params": {}},
            }

        async def update_run(self, run_id, **kw):
            pass

        async def set_run_tag(self, run_id, key, value):
            self.run_tags_set.append((run_id, key, value))

        async def transition_model_version_stage(
            self, name, version, stage, archive_existing_versions=False
        ):
            return {"name": name, "version": str(version), "current_stage": stage}

        async def delete_model_version(self, name, version):
            pass

        async def create_registered_model(self, name):
            self.create_registered_model_calls.append(name)
            return {"name": name}

        async def create_model_version(self, name, source, run_id):
            self._mv_version_counter += 1
            return {
                "name": name,
                "version": str(self._mv_version_counter),
                "run_id": run_id,
            }

        async def rename_registered_model(self, name: str, new_name: str) -> dict:
            self.rename_calls.append((name, new_name))
            return {"name": new_name}

        async def delete_registered_model(self, name: str) -> None:
            self.deleted_registered_models.append(name)

        async def search_registered_models(self, max_results=100):
            return []

        async def search_model_versions(self, filter_string=None, max_results=200):
            return []

        async def search_experiments(self, max_results=100):
            return []

        async def search_runs(
            self, experiment_ids, filter_string=None, max_results=100
        ):
            return []

    import app.routers.experiments_proxy as ep_mod
    import app.routers.jobs as jobs_mod
    import app.routers.models_registry as mr_mod
    import app.services.mlflow_client as mc

    real_mlflow_cls = mc.MlflowClient

    stub = _Stub()
    monkeypatch.setattr(mc, "MlflowClient", lambda *a, **kw: stub)
    monkeypatch.setattr(jobs_mod, "MlflowClient", lambda *a, **kw: stub)
    monkeypatch.setattr(mr_mod, "MlflowClient", lambda *a, **kw: stub)
    monkeypatch.setattr(ep_mod, "MlflowClient", lambda *a, **kw: stub)
    yield stub
    if ep_mod.MlflowClient is not real_mlflow_cls:
        ep_mod.MlflowClient = real_mlflow_cls


@pytest_asyncio.fixture
async def seed_user(user_client):
    """Return the User ORM object for the user behind user_client."""
    from app.models import User
    from sqlalchemy import select

    async with test_session_maker() as session:
        result = await session.execute(
            select(User).where(User.email == "user1@example.dev")
        )
        return result.scalar_one()


_MINIMAL_MANIFEST = {
    "detector": {"name": "upxelfdet", "version": "0.4.0", "framework": "sklearn"},
    "input": {
        "binary_format": "elf",
        "required_sections": [],
        "dataset_contract": "sample_csv",
    },
    "output": {
        "task": "binary_classification",
        "classes": ["Benign", "Malware"],
        "positive_class": "Malware",
        "score_range": [0.0, 1.0],
    },
    "resources": {
        "supports": ["cpu", "gpu2"],
        "recommended": "cpu",
        "min_memory_gib": 2,
        "gpu_required": False,
    },
    "lifecycle": {
        "stages": ["train", "evaluate", "predict"],
        "supports_serving": False,
        "supports_hpsweep": True,
        "supports_distributed": False,
        "supports_multinode": False,
    },
    "artifacts": {
        "model": {"path": "model/", "type": "dir"},
        "metrics": {"path": "metrics.json", "type": "file"},
        "predictions": {"path": "predictions.csv", "type": "file"},
    },
    "compat": {"min_python": "3.12", "min_maldet": "1.0", "schema_version": 1},
    "stages": {
        # Phase 11e: each stage carries config_class + params_schema. The default
        # schema here is intentionally permissive (no ``additionalProperties:
        # false``) so integration tests that submit arbitrary user params still
        # pass; jsonschema-rejection cases live in
        # ``tests/test_jsonschema_validate_params.py``.
        "train": {
            "config_class": "test.configs:TrainConfig",
            "params_schema": {"type": "object"},
        },
        "evaluate": {
            "config_class": "test.configs:EvaluateConfig",
            "params_schema": {"type": "object"},
        },
        "predict": {
            "config_class": "test.configs:PredictConfig",
            "params_schema": {"type": "object"},
        },
    },
}


# Phase 13b Q1: train-stage manifest mirroring elfrfdet — a params_schema with
# per-field ``default`` values, including ``max_depth: None`` to lock down the
# "default declared as null" round-trip. Reused by the detector_defaults
# round-trip tests in ``test_routers_jobs.py``.
RICH_MANIFEST_WITH_TRAIN_DEFAULTS = {
    **_MINIMAL_MANIFEST,
    "stages": {
        **_MINIMAL_MANIFEST["stages"],
        "train": {
            "config_class": "test.configs:TrainConfig",
            "params_schema": {
                "type": "object",
                "properties": {
                    "n_estimators": {"type": "integer", "default": 100},
                    "max_depth": {"type": ["integer", "null"], "default": None},
                    "random_state": {"type": "integer", "default": 42},
                },
            },
        },
    },
}


@pytest_asyncio.fixture
async def seed_detector_version(db_session, seed_user):
    """Return a callable that inserts a DetectorVersion row.

    Defaults to ``_MINIMAL_MANIFEST`` (permissive params_schema, suitable for
    most tests). Pass ``manifest=`` to override — typically with
    :data:`RICH_MANIFEST_WITH_TRAIN_DEFAULTS` for the detector_defaults
    round-trip tests in ``test_routers_jobs.py``.
    """

    async def _seed(
        name: str = "upxelfdet",
        git_tag: str = "v0.4.0",
        manifest: dict | None = None,
    ):
        from app.models import Detector, DetectorVersion
        from app.models.detector import DetectorVersionStatus

        det = Detector(
            name=name,
            display_name=name,
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
            status=DetectorVersionStatus.ACTIVE,
            manifest=manifest if manifest is not None else _MINIMAL_MANIFEST,
        )
        db_session.add(dv)
        await db_session.commit()
        return str(dv.id)

    return _seed


@pytest_asyncio.fixture
async def seed_dataset(user_client):
    from pathlib import Path

    FIXTURE_CSV = (
        Path(__file__).parent / "fixtures" / "sample_dataset.csv"
    ).read_text()

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
        json={"provider": "github", "token": "ghp_" + "A" * 24 + "ab0123456789"},
    )
    create = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    return create.json()["id"]


@pytest_asyncio.fixture
async def seed_model_version(
    db_session, seed_user, seed_detector_version, seed_dataset
):
    """Insert a ModelVersion row tied to a fresh detector_version + fake source job.

    Also promotes the seed_user to DEVELOPER so transition tests can run.
    """
    from uuid import UUID, uuid4

    from app.models import Job, ModelVersion, Role, User
    from app.models.job import JobStatus, JobType
    from app.models.model_registry import ModelVersionStage
    from sqlalchemy import update as sa_update

    async with test_session_maker() as _s:
        await _s.execute(
            sa_update(User).where(User.id == seed_user.id).values(role=Role.DEVELOPER)
        )
        await _s.commit()

    async def _seed(name: str = "upxelfdet"):
        from app.models.model_registry import RegisteredModel
        from sqlalchemy import func, select

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

        # Resolve the detector_id from the seeded DetectorVersion row.
        from app.models.detector import DetectorVersion as _DV

        dv_row = await db_session.get(_DV, UUID(dv_id_str))
        assert dv_row is not None

        # Get-or-create the RegisteredModel (owner x detector pairing).
        rm_row = (
            await db_session.execute(
                select(RegisteredModel).where(
                    RegisteredModel.owner_id == seed_user.id,
                    RegisteredModel.detector_id == dv_row.detector_id,
                )
            )
        ).scalar_one_or_none()
        if rm_row is None:
            rm_row = RegisteredModel(
                owner_id=seed_user.id,
                detector_id=dv_row.detector_id,
            )
            db_session.add(rm_row)
            await db_session.flush()

        row = await db_session.execute(
            select(func.coalesce(func.max(ModelVersion.mlflow_version), 0)).where(
                ModelVersion.registered_model_id == rm_row.id
            )
        )
        next_version = row.scalar_one() + 1

        mv = ModelVersion(
            registered_model_id=rm_row.id,
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


# ---------------------------------------------------------------------------
# Phase 13a A4 factory fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_client(client):
    """Bare async client (no auth headers set). Tests pass headers explicitly."""
    return client


@pytest_asyncio.fixture
async def auth_owner_headers():
    """Headers that identify the detector owner (DEVELOPER role).

    Seeds the user row so callers that do not use detector_factory can still
    authenticate — _make_user is idempotent.
    """
    await _make_user("dev@example.dev", role=Role.DEVELOPER)
    return {"x-test-user-email": "dev@example.dev"}


@pytest_asyncio.fixture
async def auth_other_user_headers():
    """Headers for a second user who does NOT own any detector created by the owner."""
    await _make_user("other@example.dev", role=Role.USER)
    return {"x-test-user-email": "other@example.dev"}


@pytest_asyncio.fixture
async def detector_factory(async_client, auth_owner_headers, monkeypatch):
    """Return an async callable that creates a Detector via POST /api/v1/detectors.

    Usage::

        detector = await detector_factory(name="rfdet")
        detector.id   # UUID
        detector.name # str
    """
    from app.routers import detectors as dr

    # Ensure the owner user exists before any request
    await _make_user("dev@example.dev", role=Role.DEVELOPER)

    async def _create(name: str = "det"):
        async def fake_meta(url, pat):
            return {"name": name, "description": "", "display_name": name}

        monkeypatch.setattr(dr, "_clone_and_validate", fake_meta)

        # Ensure owner has git credential (idempotent)
        await async_client.put(
            "/api/v1/users/me/git-credential",
            json={"provider": "github", "token": "ghp_" + "A" * 24 + "ab0123456789"},
            headers=auth_owner_headers,
        )
        resp = await async_client.post(
            "/api/v1/detectors",
            json={"git_url": f"https://github.com/test/{name}.git"},
            headers=auth_owner_headers,
        )
        assert resp.status_code == 201, resp.text

        class _Det:
            pass

        import uuid as _uuid

        d = _Det()
        payload = resp.json()
        d.id = _uuid.UUID(payload["id"])
        d.name = payload["name"]
        return d

    return _create


@pytest_asyncio.fixture
async def version_factory(db_session):
    """Return an async callable that inserts a DetectorVersion row directly via ORM.

    Usage::

        version = await version_factory(
            detector_id=det.id, git_tag="v1.0.0", image_digest="sha256:abc",
        )
        version.id  # UUID
    """
    from app.models import DetectorVersion
    from app.models.detector import DetectorVersionStatus

    async def _create(
        detector_id,
        git_tag: str = "v0.1.0",
        image_digest: str = "sha256:" + "a" * 64,
        status: str = "active",
    ):
        status_enum = DetectorVersionStatus(status)
        dv = DetectorVersion(
            detector_id=detector_id,
            git_tag=git_tag,
            git_sha="b" * 40,
            harbor_image=f"harbor.harbor.svc:80/detectors/det:{git_tag}",
            image_digest=image_digest,
            status=status_enum,
            manifest=None,
        )
        db_session.add(dv)
        await db_session.commit()
        await db_session.refresh(dv)
        return dv

    return _create


@pytest_asyncio.fixture
async def job_factory(db_session):
    """Return an async callable that inserts a Job row directly via ORM.

    The job is owned by the detector owner (dev@example.dev).

    Usage::

        job = await job_factory(detector_version_id=version.id, status="running")
        job.id  # UUID
    """
    import uuid as _uuid

    from app.models import Job, User
    from app.models.job import JobStatus, JobType
    from sqlalchemy import select as sa_select

    async def _create(
        detector_version_id,
        status: str = "pending",
        job_type: str = "train",
        **extra_fields,
    ):
        owner = (
            await db_session.execute(
                sa_select(User).where(User.email == "dev@example.dev")
            )
        ).scalar_one_or_none()
        if owner is None:
            owner = await _make_user("dev@example.dev", role=Role.DEVELOPER)
            # re-fetch inside this session
            owner = (
                await db_session.execute(
                    sa_select(User).where(User.email == "dev@example.dev")
                )
            ).scalar_one()

        job = Job(
            type=JobType(job_type),
            status=JobStatus(status),
            detector_version_id=detector_version_id,
            owner_id=owner.id,
            resolved_config={},
            idempotency_key=_uuid.uuid4().hex,
            **extra_fields,
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)
        return job

    return _create


# ---------------------------------------------------------------------------
# Shared model-registry fixtures (used by test_models_registry.py;
# TODO: migrate test_models_{list,get,transition,…} to use this shared fixture)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def populated(db_session):
    """Build the canonical model-registry test universe.

    Universe:
    - alice (developer), bob (developer)
    - detectors: elf-rf (owner alice), elf-cnn (owner alice)
    - alice/elf-rf has v1 (public, Production), v2 (private, Staging)
    - bob/elf-rf has v1 (private, None)
    - alice/elf-cnn has v1 (public, Production)
    """
    import uuid as _u

    from app.models import (
        Detector,
        DetectorVersion,
        Job,
        ModelVersion,
        ModelVersionStage,
        ModelVersionVisibility,
        RegisteredModel,
        User,
    )
    from app.models.job import JobStatus, JobType

    alice = User(email="alice@x.com", handle="alice", role=Role.DEVELOPER)
    bob = User(email="bob@x.com", handle="bob", role=Role.DEVELOPER)
    db_session.add_all([alice, bob])
    await db_session.flush()

    det_rf = Detector(
        name="elf-rf",
        display_name="ELF RF",
        git_url="https://github.com/x/elf-rf",
        owner_id=alice.id,
    )
    det_cnn = Detector(
        name="elf-cnn",
        display_name="ELF CNN",
        git_url="https://github.com/x/elf-cnn",
        owner_id=alice.id,
    )
    db_session.add_all([det_rf, det_cnn])
    await db_session.flush()

    dv_rf = DetectorVersion(
        detector_id=det_rf.id,
        git_tag="v1",
        git_sha="a" * 40,
        harbor_image="x/elf-rf:v1",
        image_digest="sha256:" + "0" * 64,
    )
    dv_cnn = DetectorVersion(
        detector_id=det_cnn.id,
        git_tag="v1",
        git_sha="b" * 40,
        harbor_image="x/elf-cnn:v1",
        image_digest="sha256:" + "1" * 64,
    )
    db_session.add_all([dv_rf, dv_cnn])
    await db_session.flush()

    def _job(owner: User, dv: DetectorVersion) -> Job:
        return Job(
            type=JobType.TRAIN,
            owner_id=owner.id,
            detector_version_id=dv.id,
            status=JobStatus.SUCCEEDED,
            mlflow_run_id=_u.uuid4().hex,
            resolved_config={},
            idempotency_key=_u.uuid4().hex,
        )

    rm_alice_rf = RegisteredModel(owner_id=alice.id, detector_id=det_rf.id)
    rm_bob_rf = RegisteredModel(owner_id=bob.id, detector_id=det_rf.id)
    rm_alice_cnn = RegisteredModel(owner_id=alice.id, detector_id=det_cnn.id)
    db_session.add_all([rm_alice_rf, rm_bob_rf, rm_alice_cnn])
    await db_session.flush()

    versions_to_make = [
        # (rm, version, owner, dv, visibility, stage)
        (
            rm_alice_rf,
            1,
            alice,
            dv_rf,
            ModelVersionVisibility.PUBLIC,
            ModelVersionStage.PRODUCTION,
        ),
        (
            rm_alice_rf,
            2,
            alice,
            dv_rf,
            ModelVersionVisibility.PRIVATE,
            ModelVersionStage.STAGING,
        ),
        (
            rm_bob_rf,
            1,
            bob,
            dv_rf,
            ModelVersionVisibility.PRIVATE,
            ModelVersionStage.NONE,
        ),
        (
            rm_alice_cnn,
            1,
            alice,
            dv_cnn,
            ModelVersionVisibility.PUBLIC,
            ModelVersionStage.PRODUCTION,
        ),
    ]
    for rm, ver, owner, dv, vis, stage in versions_to_make:
        j = _job(owner, dv)
        db_session.add(j)
        await db_session.flush()
        mv = ModelVersion(
            registered_model_id=rm.id,
            mlflow_version=ver,
            mlflow_run_id=j.mlflow_run_id,
            current_stage=stage,
            visibility=vis,
            detector_version_id=dv.id,
            source_job_id=j.id,
            owner_id=owner.id,
        )
        db_session.add(mv)
    await db_session.commit()

    return {
        "alice": alice,
        "bob": bob,
        "rm_alice_rf": rm_alice_rf,
        "rm_bob_rf": rm_bob_rf,
        "rm_alice_cnn": rm_alice_cnn,
    }


@pytest_asyncio.fixture
async def alice_client(populated):
    """AsyncClient authenticated as alice (DEVELOPER) from the populated universe."""
    from app.auth.cf_access import cf_access_user
    from app.db import get_async_session
    from app.main import app
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    alice = populated["alice"]

    async def override():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override

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

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"x-test-user-email": alice.email},
    ) as c:
        yield c
    # Pop only the overrides this fixture set, not all (clear() wipes other
    # fixtures' overrides if both are active in the same test).
    app.dependency_overrides.pop(get_async_session, None)
    app.dependency_overrides.pop(cf_access_user, None)


# ---------------------------------------------------------------------------
# H-3: flat /builds/{id} ACL fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def soft_deleted_detector_with_build(db_session):
    """Create a Detector + DetectorBuild pair where the detector is soft-deleted.

    The returned object exposes a single ``.build_id`` UUID attribute.
    Used by ``test_flat_build_route_404s_if_parent_detector_deleted`` to
    verify that the flat ``GET /api/v1/builds/{id}`` route 404s when the
    parent detector is soft-deleted, matching the nested route's behaviour.
    """
    import uuid as _uuid
    from datetime import UTC, datetime

    from app.models import User
    from app.models.detector import Detector, DetectorBuild

    # Seed a minimal owner user (idempotent), then re-fetch inside the
    # current session so the ORM identity map is consistent.
    await _make_user("soft-del-owner@example.dev", role=Role.USER)
    from sqlalchemy import select as _select

    owner_row = (
        await db_session.execute(
            _select(User).where(User.email == "soft-del-owner@example.dev")
        )
    ).scalar_one()

    detector = Detector(
        name=f"soft-del-det-{_uuid.uuid4().hex[:8]}",
        display_name="Soft Deleted Detector",
        git_url="https://github.com/test/soft-del-det.git",
        owner_id=owner_row.id,
        deleted_at=datetime.now(UTC),
    )
    db_session.add(detector)
    await db_session.flush()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v1.0.0",
        triggered_by_id=owner_row.id,
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)

    class _Result:
        pass

    r = _Result()
    r.build_id = build.id
    return r
