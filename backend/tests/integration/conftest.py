"""Integration-tier fixtures: aiosqlite + autouse mocks for MLflow / K8s /
Redis / Discord HTTP. Applies to backend/tests/integration/ subtree only —
heavy tier (testcontainers) and contract tier (schemathesis) have their
own conftests under their respective directories.

These fixtures used to live in backend/tests/conftest.py; this file is
the result of the R1 refactor (Phase 1 D1.2)."""

import pytest
import pytest_asyncio
from sqlalchemy import select

from tests.conftest import test_session_maker


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
        def __init__(self):
            # M-token-secret-owner: record ownerReferences patches for assertion.
            self.secret_patches: list[tuple[str, str, dict]] = []

        def create_namespaced_secret(self, namespace, body, **kw):
            return body

        def delete_namespaced_secret(self, name, namespace, **kw):
            pass

        def patch_namespaced_secret(self, name, namespace, body, **kw):
            # M-token-secret-owner: record ownerReferences patches for assertion.
            self.secret_patches.append((name, namespace, body))
            return body

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
            import uuid as _uu

            name = (
                (body.get("metadata") or {}).get("name")
                if isinstance(body, dict)
                else body.metadata.name
            )
            # M-token-secret-owner: dispatch_job_to_volcano reads metadata.uid
            # from this response to populate Secret ownerReferences. Real K8s
            # always populates uid on create; mirror that here.
            if isinstance(body, dict):
                body.setdefault("metadata", {}).setdefault("uid", str(_uu.uuid4()))
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

    async with test_session_maker() as session:
        result = await session.execute(
            select(User).where(User.email == "user1@example.dev")
        )
        return result.scalar_one()
