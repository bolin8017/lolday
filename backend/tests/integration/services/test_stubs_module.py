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


def test_stub_batch_read_returns_status_with_succeeded_and_failed_none():
    """Reconciler reads ``job.status.succeeded`` / ``job.status.failed`` to
    decide whether a build vcjob has terminated. The stub returns both as
    ``None`` (i.e. still running) so the reconciler treats every stubbed
    vcjob as in-flight — that's the conservative default."""
    batch = StubBatch()
    batch.create_namespaced_job("ns", {"metadata": {"name": "j1"}})
    job = batch.read_namespaced_job("j1", "ns")
    assert job.status.succeeded is None
    assert job.status.failed is None


def test_stub_core_create_and_delete_secret_are_noops():
    """Both calls return without side-effect; the test pins the contract
    so a future refactor that adds state can't silently break the
    reconciler's expectation that delete is a no-op on missing keys."""
    core = StubCore()
    result = core.create_namespaced_secret("ns", {"metadata": {"name": "s"}})
    assert result == {"metadata": {"name": "s"}}
    # delete is silently idempotent.
    core.delete_namespaced_secret("anything", "ns")


def test_stub_core_pod_log_returns_empty_string():
    """``reconciler/log_capture.py`` calls ``read_namespaced_pod_log``; the
    stub returns ``""`` so the capture loop produces an empty log file
    rather than crashing on AttributeError."""
    core = StubCore()
    assert core.read_namespaced_pod_log() == ""


def test_stub_core_list_namespaced_pod_returns_empty_items():
    """Sibling to ``list_namespaced_secret`` — the reconciler iterates
    ``items`` on every tick."""
    core = StubCore()
    result = core.list_namespaced_pod("ns")
    assert list(result.items) == []


def test_stub_volcano_create_cluster_custom_object_idempotent():
    """``ensure_user_queue`` creates a cluster-scoped Volcano Queue; the
    stub stores it under the same name space as namespaced objects so
    follow-up ``list_namespaced_custom_object`` calls see it."""
    volcano = StubVolcano()
    body = {"metadata": {"name": "q-1"}, "spec": {"weight": 1}}
    volcano.create_cluster_custom_object(
        "scheduling.volcano.sh", "v1beta1", "queues", body
    )
    # The recorded object is the same body (the stub is idempotent).
    listed = volcano.list_namespaced_custom_object(
        "scheduling.volcano.sh", "v1beta1", "ns", "queues"
    )
    assert listed["items"] == [body]


def test_stub_volcano_delete_returns_empty_dict():
    """Delete is a no-op in the stub; the real API returns the deleted
    object's status, but the reconciler only checks the call did not
    raise. Pin the empty-dict return shape so a future refactor that
    starts inspecting the response doesn't break."""
    volcano = StubVolcano()
    assert (
        volcano.delete_namespaced_custom_object(
            "batch.volcano.sh", "v1alpha1", "ns", "jobs", "missing"
        )
        == {}
    )


async def test_stub_mlflow_get_run_returns_finished_with_run_id_threaded():
    """``reconciler/jobs._finalize_mlflow_run`` calls ``get_run`` and reads
    ``info.run_id`` / ``info.status`` — the stub must round-trip the
    requested run id and report FINISHED so the reconciler advances
    state."""
    stub = StubMlflowClient()
    out = await stub.get_run("run-xyz")
    assert out["info"]["run_id"] == "run-xyz"
    assert out["info"]["status"] == "FINISHED"
    assert out["data"]["metrics"] == {"accuracy": 0.9}


async def test_stub_mlflow_set_run_tag_records_the_call():
    """``set_run_tag`` is called from ``reconciler/jobs.py`` on every
    state transition; the stub records the call so the live-stack spec
    can later assert the right transitions fired."""
    stub = StubMlflowClient()
    await stub.set_run_tag("run-1", "lolday.stage", "succeeded")
    assert ("run-1", "lolday.stage", "succeeded") in stub.run_tags_set


async def test_stub_mlflow_search_methods_return_deterministic_shapes():
    """Each search-* coroutine is hit by a Playwright spec — pin the
    return shapes so a chart-only re-render does not have to inspect
    the live-stack to confirm the contract."""
    stub = StubMlflowClient()
    assert await stub.search_registered_models() == []
    assert await stub.search_model_versions(filter_string="x") == []
    assert await stub.search_runs(["exp-fixture"]) == []
    exps = await stub.search_experiments()
    assert len(exps) == 1
    assert exps[0]["experiment_id"] == "exp-fixture"
    # The seeded experiment name mirrors `<owner>/<detector>` per the
    # module docstring; a refactor that renames it must update the
    # E2E mobile / a11y specs in lock-step.
    assert "/" in exps[0]["name"]


async def test_stub_mlflow_create_run_increments_counter_and_records_tags():
    """``reconciler/jobs._start_mlflow_run`` calls ``create_run``; the
    stub records the tags so the live-stack spec can assert the right
    `lolday.*` tag set was attached."""
    stub = StubMlflowClient()
    tags = [{"key": "lolday.user_id", "value": "u-1"}]
    run_id_1 = await stub.create_run("exp-1", start_time_ms=0, tags=tags)
    run_id_2 = await stub.create_run("exp-1", start_time_ms=0)
    assert run_id_1 != run_id_2  # counter advances
    assert ("exp-1", tags) in stub.runs_created
    # tags=None is normalised to []
    assert ("exp-1", []) in stub.runs_created


async def test_stub_mlflow_set_experiment_tag_records_the_call():
    """Sibling to ``set_run_tag`` for experiment-level tags."""
    stub = StubMlflowClient()
    await stub.set_experiment_tag("exp-1", "lolday.owner", "u-1")
    assert ("exp-1", "lolday.owner", "u-1") in stub.run_tags_set


async def test_stub_mlflow_update_run_is_a_noop():
    """``reconciler/jobs._finalize_mlflow_run`` calls ``update_run`` with
    status / end_time. The stub must not raise."""
    stub = StubMlflowClient()
    # Should not raise; the return value is unused by callers.
    assert await stub.update_run("run-1", status="FINISHED") is None


async def test_stub_mlflow_transition_model_version_stage_round_trips():
    """The registry promotion path reads ``current_stage`` from the
    return — pin the shape so it stays useful."""
    stub = StubMlflowClient()
    out = await stub.transition_model_version_stage("m", 3, "Production")
    assert out == {"name": "m", "version": "3", "current_stage": "Production"}


async def test_stub_mlflow_delete_model_version_is_a_noop():
    stub = StubMlflowClient()
    assert await stub.delete_model_version("m", 1) is None


async def test_stub_mlflow_create_registered_model_records_the_name():
    stub = StubMlflowClient()
    out = await stub.create_registered_model("m-1")
    assert out == {"name": "m-1"}
    assert "m-1" in stub.create_registered_model_calls


async def test_stub_mlflow_create_model_version_increments_per_instance_counter():
    """The per-instance counter is what makes the stub usable across
    multiple model versions in the same Playwright spec."""
    stub = StubMlflowClient()
    v1 = await stub.create_model_version("m", "s3://x", "run-1")
    v2 = await stub.create_model_version("m", "s3://x", "run-2")
    assert v1["version"] == "1"
    assert v2["version"] == "2"
    assert v1["run_id"] == "run-1"


async def test_stub_mlflow_rename_and_delete_registered_model_record_calls():
    """``services/model_registry.py`` is the only consumer of these
    on the rename / delete paths; the recorded list lets the live-stack
    spec assert the right model was touched."""
    stub = StubMlflowClient()
    out = await stub.rename_registered_model("old", "new")
    assert out == {"name": "new"}
    assert ("old", "new") in stub.rename_calls

    await stub.delete_registered_model("m-to-delete")
    assert "m-to-delete" in stub.deleted_registered_models


async def test_stub_remote_tags_and_user_pat_helpers_return_placeholders():
    """``stub_list_remote_tags`` + ``stub_get_user_pat`` are bound into
    ``services.git`` under ``SPEC_LANE_STUBS=true``; the values they
    return drive the trigger-build E2E spec."""
    from app.services._stubs import (
        STUB_REMOTE_TAGS,
        stub_get_user_pat,
        stub_list_remote_tags,
    )

    tags = await stub_list_remote_tags("owner", "repo")
    # Must be a copy, not the module-level list itself, so a mutation by
    # the consumer doesn't leak across calls.
    assert tags == STUB_REMOTE_TAGS
    assert tags is not STUB_REMOTE_TAGS

    pat = await stub_get_user_pat(session=None, user_id="anyone")
    assert isinstance(pat, str) and pat
