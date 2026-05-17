"""Direct-call tests for ``app.services._stubs``.

The shared module is imported by both the integration conftest (per-test
autouse instances) and the FastAPI lifespan when ``SPEC_LANE_STUBS=true``
(singletons). These tests guard the behavioural contract so a refactor
in one consumer cannot silently change the other.

Spec: docs/superpowers/specs/2026-05-17-frontend-slow-stub-layer-design.md §6.6.
"""

from __future__ import annotations

import pytest
from app.services._stubs import (
    StubBatch,
    StubCore,
    StubMlflowClient,
    StubVolcano,
    safe_load_config,
)
from kubernetes.client.exceptions import ApiException


def test_stub_batch_create_then_read_404():
    batch = StubBatch()
    batch.create_namespaced_job("ns", {"metadata": {"name": "job-1"}})
    batch.delete_namespaced_job("job-1", "ns")
    with pytest.raises(ApiException) as exc_info:
        batch.read_namespaced_job("job-1", "ns")
    assert exc_info.value.status == 404


def test_stub_core_secret_patches_recorded():
    core = StubCore()
    body = {"metadata": {"ownerReferences": ["fake"]}}
    core.patch_namespaced_secret("sec-1", "ns", body)
    assert core.secret_patches == [("sec-1", "ns", body)]


def test_stub_core_list_namespaced_secret_returns_empty_items():
    # Contract used by reconciler/orphans._sweep_orphan_token_secrets_in_namespace:
    # the call must return an object with an ``items`` iterable so the
    # ``for sec in secrets.items`` loop is a no-op when no fixtures inject
    # secrets.
    core = StubCore()
    result = core.list_namespaced_secret("lolday-jobs")
    assert list(result.items) == []


def test_stub_volcano_create_then_list():
    volcano = StubVolcano()
    volcano.create_namespaced_custom_object(
        "batch.volcano.sh",
        "v1alpha1",
        "ns",
        "jobs",
        {"metadata": {"name": "vcjob-1"}},
    )
    listed = volcano.list_namespaced_custom_object(
        "batch.volcano.sh", "v1alpha1", "ns", "jobs"
    )
    assert len(listed["items"]) == 1


def test_stub_volcano_get_returns_404_by_default():
    volcano = StubVolcano()
    with pytest.raises(ApiException) as exc_info:
        volcano.get_namespaced_custom_object(
            "batch.volcano.sh", "v1alpha1", "ns", "jobs", "missing"
        )
    assert exc_info.value.status == 404


async def test_stub_mlflow_get_or_create_experiment_increments_counter():
    stub = StubMlflowClient()
    exp_id_1 = await stub.get_or_create_experiment("exp-A")
    exp_id_2 = await stub.get_or_create_experiment("exp-B")
    assert exp_id_1 != exp_id_2
    assert "exp-A" in stub.experiment_creates
    assert "exp-B" in stub.experiment_creates


def test_safe_load_config_swallows_config_exception():
    # The contract: when no kubeconfig is available, the function MUST NOT
    # raise. The CI runner has no kubeconfig; on a local workstation the
    # kubernetes lib loads the real config (harmless because the live-stack
    # never reaches a real K8s call when stubs are installed).
    safe_load_config()
