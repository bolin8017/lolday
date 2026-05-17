"""Shared in-process stubs for Kubernetes + MLflow.

Two consumers:

- ``backend/tests/integration/conftest.py`` — autouse fixtures
  instantiate per-test stubs via ``monkeypatch`` (isolation).
- ``backend/app/main.py`` lifespan — when ``settings.SPEC_LANE_STUBS``
  is true, ``_install_spec_lane_stubs(app)`` installs singletons on
  ``app.state`` so the reconciler / FIFO scheduler share state with
  the route handlers (mirrors a real K8s API server).

Production refuses boot when ``SPEC_LANE_STUBS=true`` (see
``Settings.validate_sso_config``). Importing this module from a
production code path outside that flag is a bug — code review should
flag it. The leading underscore on the module name is the convention
signal.
"""

from __future__ import annotations

import contextlib
import uuid

# Single source of truth for module-level rebinding. Both consumers
# walk this list to patch every ``from app.services.k8s import …``
# rebound name. Add a new entry whenever a new caller module imports
# ``batch_v1`` / ``core_v1`` / ``volcano_v1alpha1`` via ``from``.
CALLER_MODULE_REBIND_TARGETS: list[tuple[str, str]] = [
    ("app.services.harbor_init", "core_v1"),
    ("app.services.cluster_status", "volcano_v1alpha1"),
    # Phase 6d: vcjob + token Secret creation moved into job_dispatch;
    # patch the new home so callers don't reach a live cluster.
    ("app.services.job_dispatch", "core_v1"),
    ("app.services.job_dispatch", "volcano_v1alpha1"),
    ("app.routers.detectors", "batch_v1"),
    ("app.routers.detectors", "core_v1"),
    ("app.routers.jobs", "batch_v1"),
    ("app.routers.jobs", "core_v1"),
    ("app.reconciler.builds", "batch_v1"),
    ("app.reconciler.builds", "core_v1"),
    ("app.reconciler.jobs", "core_v1"),
    ("app.reconciler.jobs", "volcano_v1alpha1"),
    ("app.reconciler.log_capture", "core_v1"),
    ("app.reconciler.orphans", "core_v1"),
    ("app.reconciler.orphans", "volcano_v1alpha1"),
]


def safe_load_config() -> None:
    """Try in-cluster, then user-local kubeconfig; swallow if neither exists.

    The kubernetes client raises ``ConfigException`` when neither path is
    available (CI runners with no kubeconfig). The pytest tier swallowed
    this in ``_mock_k8s_load_config``; the live-stack needs the same
    guarantee.
    """
    from kubernetes import config as _kube_config
    from kubernetes.config.config_exception import ConfigException

    try:
        _kube_config.load_incluster_config()
    except ConfigException:
        with contextlib.suppress(ConfigException):
            _kube_config.load_kube_config()


class StubBatch:
    """In-memory replacement for ``kubernetes.client.BatchV1Api``."""

    def __init__(self) -> None:
        self.jobs: dict = {}

    def create_namespaced_job(self, namespace, body, **kw):
        name = (
            body["metadata"]["name"] if isinstance(body, dict) else body.metadata.name
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


class StubCore:
    """In-memory replacement for ``kubernetes.client.CoreV1Api``."""

    def __init__(self) -> None:
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


class StubVolcano:
    """In-memory replacement for ``kubernetes.client.CustomObjectsApi``
    scoped to the Volcano CRDs (``batch.volcano.sh/v1alpha1``).

    Phase 7.3 routed training jobs through Volcano CRDs via
    ``CustomObjectsApi.create_namespaced_custom_object()``. Without this
    stub, ``test_jobs`` and ``test_rate_limits`` POST /api/v1/jobs calls
    leak real ``batch.volcano.sh/v1alpha1 Job`` CRs onto whatever cluster
    kubectl is pointed at (observed: 515 stale Pending Jobs on server30
    from a single dev run).
    """

    def __init__(self) -> None:
        self.objects: dict = {}

    def create_namespaced_custom_object(
        self, group, version, namespace, plural, body, **kw
    ):
        name = (
            (body.get("metadata") or {}).get("name")
            if isinstance(body, dict)
            else body.metadata.name
        )
        # M-token-secret-owner: dispatch_job_to_volcano reads metadata.uid
        # from this response to populate Secret ownerReferences. Real K8s
        # always populates uid on create; mirror that here.
        if isinstance(body, dict):
            body.setdefault("metadata", {}).setdefault("uid", str(uuid.uuid4()))
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


class StubMlflowClient:
    """In-memory replacement for ``app.services.mlflow_client.MlflowClient``.

    Implements every coroutine the production client exposes that route
    handlers / the reconciler call. Counters are class-level so multiple
    instances in the same process produce monotonically increasing IDs
    (matches the original pytest stub semantics).
    """

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
        StubMlflowClient.exp_counter += 1
        self.experiment_creates.append(name)
        return f"exp-{StubMlflowClient.exp_counter}"

    async def create_run(self, experiment_id, *, start_time_ms, tags=None):
        StubMlflowClient.run_counter += 1
        self.runs_created.append((experiment_id, list(tags or [])))
        return f"run-{StubMlflowClient.run_counter}"

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

    async def search_runs(self, experiment_ids, filter_string=None, max_results=100):
        return []
