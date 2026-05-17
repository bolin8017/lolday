"""Integration-tier fixtures: aiosqlite + autouse mocks for MLflow / K8s /
Redis / Discord HTTP. Applies to backend/tests/integration/ subtree only —
heavy tier (testcontainers) and contract tier (schemathesis) have their
own conftests under their respective directories.

These fixtures used to live in backend/tests/conftest.py; this file is
the result of the R1 refactor (Phase 1 D1.2).

T10 (R1 slim): seed_* fixtures, factory fixtures (detector_factory,
version_factory, job_factory), and model-registry helpers (populated,
alice_client, soft_deleted_detector_with_build) moved here from root
conftest so root becomes single-purpose (auth + DB session + hooks)."""

import pytest
import pytest_asyncio
from sqlalchemy import select

from tests.conftest import test_session_maker


@pytest.fixture(autouse=True)
def _mock_k8s_load_config(monkeypatch):
    """Skip loading real kubeconfig in tests.

    Production code paths reach ``app.services.k8s.load_config()`` via
    service helpers; tests already mock the downstream API calls. Without
    this no-op patch, tests fail in CI (no in-cluster config, no
    ``~/.kube/config``) even though they don't actually need cluster
    access. The shared shim lives in ``app.services._stubs`` and is also
    installed by the FastAPI lifespan when ``SPEC_LANE_STUBS=true``.
    """
    from app.services._stubs import safe_load_config

    monkeypatch.setattr("app.services.k8s.load_config", safe_load_config)


@pytest.fixture(autouse=True)
def fake_redis_for_rate_limit(monkeypatch):
    """Autouse fakeredis so rate_limit service uses an in-memory store per test."""
    from fakeredis.aioredis import FakeRedis

    fake = FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.services.rate_limit._redis", fake)
    yield


@pytest.fixture(autouse=True)
def mock_k8s_batch(monkeypatch):
    """Autouse: replace kubernetes BatchV1Api + CoreV1Api + Volcano CRDs
    with in-memory stubs.

    Stub classes live in ``app.services._stubs`` so the FastAPI lifespan
    can install the same shapes when ``SPEC_LANE_STUBS=true``. This fixture
    keeps the pytest-specific ``monkeypatch`` ceremony (per-test instances,
    function-scope isolation) while sharing class definitions with the
    live-stack.
    """
    from app.services._stubs import (
        CALLER_MODULE_REBIND_TARGETS,
        StubBatch,
        StubCore,
        StubVolcano,
    )

    # Source-of-truth rebinds in app.services.k8s itself.
    monkeypatch.setattr("app.services.k8s.batch_v1", lambda: StubBatch())
    monkeypatch.setattr("app.services.k8s.core_v1", lambda: StubCore())
    monkeypatch.setattr("app.services.k8s.volcano_v1alpha1", lambda: StubVolcano())

    # Rebound `from app.services.k8s import core_v1` (etc.) names in every
    # caller module — Python's `from ... import ...` creates a new
    # module-local binding that is NOT updated by patching the source.
    # See spec 2026-05-17-frontend-slow-stub-layer-design.md §4.1.
    name_to_factory = {
        "batch_v1": StubBatch,
        "core_v1": StubCore,
        "volcano_v1alpha1": StubVolcano,
    }
    for module_path, name in CALLER_MODULE_REBIND_TARGETS:
        factory = name_to_factory[name]
        monkeypatch.setattr(f"{module_path}.{name}", lambda f=factory: f())


@pytest.fixture(autouse=True)
def mock_mlflow(request):
    if "no_mock_mlflow" in request.keywords:
        # Tests that opt out of the stub (marked no_mock_mlflow) expect the real
        # MlflowClient to be reachable via Depends(get_mlflow) so they can
        # intercept at the HTTP layer (e.g. via respx.MockRouter).
        # app.state.mlflow is set by the FastAPI lifespan, which does NOT run
        # when tests use ASGITransport directly. Install a real client backed by
        # a fresh httpx.AsyncClient so get_mlflow(request) succeeds; respx will
        # intercept the outbound HTTP calls per test.
        import httpx
        from app.config import settings
        from app.main import app as fastapi_app
        from app.services.mlflow_client import MlflowClient

        _test_http = httpx.AsyncClient()
        fastapi_app.state.mlflow = MlflowClient.from_settings(settings, _test_http)
        yield
        # Leave app.state.mlflow in place — teardown may trigger aclose warnings
        # but httpx silently drops un-closed clients; no explicit cleanup needed.
        return

    from app.deps import get_mlflow
    from app.main import app as fastapi_app
    from app.services._stubs import StubMlflowClient

    stub = StubMlflowClient()
    # Override Depends(get_mlflow) so all routers using the new DI path receive
    # the stub. This is the primary mock path after T13 migration.
    fastapi_app.dependency_overrides[get_mlflow] = lambda: stub
    yield stub
    fastapi_app.dependency_overrides.pop(get_mlflow, None)


@pytest_asyncio.fixture
async def seed_user(user_client):
    """Return the User ORM object for the user behind user_client."""
    from app.models import User

    async with test_session_maker() as session:
        result = await session.execute(
            select(User).where(User.email == "user1@example.dev")
        )
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Seed fixtures (moved from root conftest T10)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_detector_version(db_session, seed_user):
    """Return a callable that inserts a DetectorVersion row.

    Defaults to ``_MINIMAL_MANIFEST`` (permissive params_schema, suitable for
    most tests). Pass ``manifest=`` to override — typically with
    :data:`~tests.fixtures.manifests.RICH_MANIFEST_WITH_TRAIN_DEFAULTS` for the
    detector_defaults round-trip tests in ``test_routers_jobs.py``.
    """
    from tests.fixtures.manifests import _MINIMAL_MANIFEST

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
        Path(__file__).parent.parent / "fixtures" / "sample_dataset.csv"
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
# Phase 13a A4 factory fixtures (moved from root conftest T10)
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
    from app.models import Role

    from tests.conftest import _make_user

    await _make_user("dev@example.dev", role=Role.DEVELOPER)
    return {"x-test-user-email": "dev@example.dev"}


@pytest_asyncio.fixture
async def auth_other_user_headers():
    """Headers for a second user who does NOT own any detector created by the owner."""
    from app.models import Role

    from tests.conftest import _make_user

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
    from app.models import Role
    from app.routers import detectors as dr

    from tests.conftest import _make_user

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

    from app.models import Job, Role, User
    from app.models.job import JobStatus, JobType
    from sqlalchemy import select as sa_select

    from tests.conftest import _make_user

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
# Shared model-registry fixtures (moved from root conftest T10)
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
        Role,
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
    from app.models import User
    from fastapi import Depends, HTTPException, Request
    from httpx import ASGITransport, AsyncClient
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
# H-3: flat /builds/{id} ACL fixture (moved from root conftest T10)
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

    from app.models import Role, User
    from app.models.detector import Detector, DetectorBuild
    from sqlalchemy import select as _select

    from tests.conftest import _make_user

    # Seed a minimal owner user (idempotent), then re-fetch inside the
    # current session so the ORM identity map is consistent.
    await _make_user("soft-del-owner@example.dev", role=Role.USER)

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
