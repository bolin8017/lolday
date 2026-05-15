"""Unit + integration tests for `app.routers.jobs` helpers and submit-time
manifest preflight.

The integration cases drive the full POST /api/v1/jobs path through the
test client to exercise the HTTP 400 branches in the real router (where a
unit test on ``_strategy_from_manifest`` alone would miss the FastAPI
exception-handling glue).
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from app.models.job import JobType
from app.services.jobs_dispatch import _strategy_from_manifest
from app.services.jobs_params_validate import resolve_detector_defaults
from maldet.manifest import DetectorManifest

from tests.fixtures.manifests import RICH_MANIFEST_WITH_TRAIN_DEFAULTS

# ---------------------------------------------------------------------------
# Unit tests for _strategy_from_manifest
# ---------------------------------------------------------------------------


def _manifest(supports_distributed) -> DetectorManifest:
    """Build a DetectorManifest with an arbitrary supports_distributed value.

    We bypass strict pydantic validation when the caller wants to drop in a
    value the schema would reject (e.g. an unknown literal); see
    ``test_unknown_strategy_raises``.
    """
    data = {
        "detector": {"name": "d", "version": "1", "framework": "sklearn"},
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
            "supports": ["cpu"],
            "recommended": "cpu",
            "min_memory_gib": 1,
            "gpu_required": False,
        },
        "lifecycle": {
            "stages": ["train", "evaluate", "predict"],
            "supports_serving": False,
            "supports_hpsweep": True,
            "supports_distributed": supports_distributed,
            "supports_multinode": False,
        },
        "artifacts": {
            "model": {"path": "model/", "type": "dir"},
            "metrics": {"path": "metrics.json", "type": "file"},
            "predictions": {"path": "predictions.csv", "type": "file"},
        },
        "compat": {"min_python": "3.12", "min_maldet": "1.0", "schema_version": 1},
        "stages": {},
    }
    return DetectorManifest.model_validate(data)


@pytest.mark.parametrize("strategy", ["ddp", "fsdp", "deepspeed"])
def test_strategy_from_manifest_passes_known_strings(strategy: str) -> None:
    """Each platform-recognised distributed strategy passes through verbatim
    so Lightning's strategy plug-in selector sees the exact token."""
    m = _manifest(strategy)
    assert _strategy_from_manifest(m.model_dump()) == strategy


def test_strategy_from_manifest_unknown_strategy_raises() -> None:
    """Pydantic constrains the literal at schema time, but if the API ever
    accepts a richer set later, ``_strategy_from_manifest`` must still be
    the source of truth on the platform side. Pass an unknown literal in
    the dict directly — this matches the realistic runtime path where a
    stored manifest from a future schema or a corrupted DB row reaches the
    helper without going through pydantic re-validation."""
    m = _manifest("ddp").model_dump()
    m["lifecycle"]["supports_distributed"] = "horovod"

    with pytest.raises(ValueError, match="not a known strategy"):
        _strategy_from_manifest(m)


@pytest.mark.parametrize("bool_val", [True, False])
def test_strategy_from_manifest_bool_falls_back_to_ddp(bool_val: bool) -> None:
    """The boolean form is the legacy / opt-out shape from
    ``maldet < 1.1``. Lightning ignores ``strategy=ddp`` when GPU count <= 1,
    so falling back to ``"ddp"`` is safe regardless of resource_profile."""
    m = _manifest(bool_val)
    assert _strategy_from_manifest(m.model_dump()) == "ddp"


def test_strategy_from_manifest_none_falls_back_to_ddp() -> None:
    """Phase 11b made ``DetectorVersion.manifest`` nullable for legacy rows.
    The helper must accept ``None`` and surface the safe ``"ddp"`` default."""
    assert _strategy_from_manifest(None) == "ddp"


# ---------------------------------------------------------------------------
# Integration: POST /api/v1/jobs through the FastAPI router for manifest
# preflight HTTP 400 paths.
# ---------------------------------------------------------------------------


def _gpu2_manifest_dict() -> dict:
    """Minimal manifest dict accepting GPU2 (so the resource-profile guard
    doesn't preempt the strategy check)."""
    return {
        "detector": {"name": "d", "version": "1", "framework": "sklearn"},
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
            "supports": ["cpu", "gpu1", "gpu2"],
            "recommended": "gpu2",
            "min_memory_gib": 4,
            "gpu_required": False,
        },
        "lifecycle": {
            "stages": ["train", "evaluate", "predict"],
            "supports_serving": False,
            "supports_hpsweep": True,
            "supports_distributed": "ddp",
            "supports_multinode": False,
        },
        "artifacts": {
            "model": {"path": "model/", "type": "dir"},
            "metrics": {"path": "metrics.json", "type": "file"},
            "predictions": {"path": "predictions.csv", "type": "file"},
        },
        "compat": {"min_python": "3.12", "min_maldet": "1.0", "schema_version": 1},
        "stages": {},
    }


@pytest.mark.asyncio
async def test_submit_job_rejects_unknown_distributed_strategy_400(
    user_client, db_session, seed_user, seed_dataset
) -> None:
    """When a stored manifest's ``supports_distributed`` is an unknown
    string (e.g. via a future maldet schema we don't yet support), the
    submit endpoint must surface a 400 with the strategy name rather than
    crashing into the Volcano launch path."""
    from uuid import uuid4

    from app.models import Detector, DetectorVersion
    from app.models.detector import DetectorVersionStatus

    # Build a manifest dict with a Pydantic-valid base, then poke an unknown
    # strategy directly into the DB JSON column — this matches what would
    # land if a build was promoted under a newer schema.
    bad = deepcopy(_gpu2_manifest_dict())
    bad["lifecycle"]["supports_distributed"] = "horovod"

    det = Detector(
        name=f"horovod-{uuid4().hex[:6]}",
        display_name="horovod",
        git_url="https://github.com/x/horovod.git",
        owner_id=seed_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v0.1.0",
        git_sha="a" * 40,
        harbor_image=f"harbor/detectors/{det.name}:v0.1.0",
        image_digest="sha256:" + "a" * 64,
        status=DetectorVersionStatus.ACTIVE,
        manifest=bad,
    )
    db_session.add(dv)
    await db_session.commit()

    train_ds = await seed_dataset(name="trh")
    test_ds = await seed_dataset(name="teh")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": str(dv.id),
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {},
            "resource_profile": "gpu2",
        },
    )
    # Either 400 from the manifest preflight (stored manifest schema rejects
    # the literal — would surface as "stored manifest invalid") or 400 from
    # ``_strategy_from_manifest`` if the value bypasses validation. Both
    # paths are correct fail-closed behaviour; assert the status only.
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_submit_job_rejects_legacy_detector_without_manifest_400(
    user_client, db_session, seed_user, seed_dataset
) -> None:
    """A DetectorVersion seeded before Phase 11b will have ``manifest=None``.
    The submit endpoint must surface a clear 400 telling the user to rebuild
    against maldet v1.0+, not silently fall through to the renderer."""
    from uuid import uuid4

    from app.models import Detector, DetectorVersion
    from app.models.detector import DetectorVersionStatus

    det = Detector(
        name=f"legacy-{uuid4().hex[:6]}",
        display_name="legacy",
        git_url="https://github.com/x/legacy.git",
        owner_id=seed_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v0.1.0",
        git_sha="a" * 40,
        harbor_image=f"harbor/detectors/{det.name}:v0.1.0",
        image_digest="sha256:" + "a" * 64,
        status=DetectorVersionStatus.ACTIVE,
        manifest=None,  # Phase 11b made this nullable for legacy rows
    )
    db_session.add(dv)
    await db_session.commit()

    train_ds = await seed_dataset(name="trl")
    test_ds = await seed_dataset(name="tel")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": str(dv.id),
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert r.status_code == 400, r.text
    assert "manifest" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_submit_job_rejects_corrupt_manifest_400(
    user_client, db_session, seed_user, seed_dataset
) -> None:
    """A stored manifest whose JSON shape doesn't deserialise into
    DetectorManifest must surface as 400 with "stored manifest invalid"
    rather than a 500."""
    from uuid import uuid4

    from app.models import Detector, DetectorVersion
    from app.models.detector import DetectorVersionStatus

    det = Detector(
        name=f"corrupt-{uuid4().hex[:6]}",
        display_name="corrupt",
        git_url="https://github.com/x/corrupt.git",
        owner_id=seed_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v0.1.0",
        git_sha="a" * 40,
        harbor_image=f"harbor/detectors/{det.name}:v0.1.0",
        image_digest="sha256:" + "a" * 64,
        status=DetectorVersionStatus.ACTIVE,
        manifest={"detector": "wrong shape"},  # not a DetectorManifest dict
    )
    db_session.add(dv)
    await db_session.commit()

    train_ds = await seed_dataset(name="trc")
    test_ds = await seed_dataset(name="tec")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": str(dv.id),
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert r.status_code == 400, r.text
    assert "stored manifest invalid" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Phase 13b B1 — GET /api/v1/jobs/{job_id}/prediction-summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prediction_summary_endpoint_returns_cached(
    async_client,
    detector_factory,
    version_factory,
    job_factory,
    auth_owner_headers,
) -> None:
    """Happy path: predict job with cached summary returns 200 + body."""
    detector = await detector_factory(name="psum-ok")
    version = await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    summary = {
        "total": 100,
        "distribution": {"Malware": 60, "Benign": 40},
        "duration_seconds": 12.0,
    }
    job = await job_factory(
        detector_version_id=version.id,
        status="succeeded",
        job_type="predict",
        summary_metrics={"prediction_summary": summary},
    )

    resp = await async_client.get(
        f"/api/v1/jobs/{job.id}/prediction-summary",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == summary


@pytest.mark.asyncio
async def test_prediction_summary_endpoint_404_when_unavailable(
    async_client,
    detector_factory,
    version_factory,
    job_factory,
    auth_owner_headers,
) -> None:
    """Predict job without cached summary → 404 with code summary_unavailable."""
    detector = await detector_factory(name="psum-miss")
    version = await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    job = await job_factory(
        detector_version_id=version.id,
        status="succeeded",
        job_type="predict",
        # summary_metrics omitted: simulate legacy / failed predict run
    )

    resp = await async_client.get(
        f"/api/v1/jobs/{job.id}/prediction-summary",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "summary_unavailable"


@pytest.mark.asyncio
async def test_prediction_summary_endpoint_400_for_non_predict(
    async_client,
    detector_factory,
    version_factory,
    job_factory,
    auth_owner_headers,
) -> None:
    """Train job → 400 with code not_predict_job, even with summary_metrics set."""
    detector = await detector_factory(name="psum-train")
    version = await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    job = await job_factory(
        detector_version_id=version.id,
        status="succeeded",
        job_type="train",
        summary_metrics={"prediction_summary": {"total": 1}},
    )

    resp = await async_client.get(
        f"/api/v1/jobs/{job.id}/prediction-summary",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "not_predict_job"


@pytest.mark.asyncio
async def test_prediction_summary_endpoint_404_for_other_user(
    async_client,
    detector_factory,
    version_factory,
    job_factory,
    auth_other_user_headers,
) -> None:
    """Non-owner non-admin → 404 (does not leak existence)."""
    detector = await detector_factory(name="psum-cross")
    version = await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    job = await job_factory(
        detector_version_id=version.id,
        status="succeeded",
        job_type="predict",
        summary_metrics={"prediction_summary": {"total": 1}},
    )

    resp = await async_client.get(
        f"/api/v1/jobs/{job.id}/prediction-summary",
        headers=auth_other_user_headers,
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Phase 13b B3 — submit_job round-trips raw user_params on JobRead
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_job_records_user_params(
    user_client, seed_detector_version, seed_dataset
) -> None:
    """Phase 13b B3: submit_job persists the raw request `params` dict on
    `Job.user_params` so the resolved-config UI can highlight overrides
    against the manifest defaults. Round-trip via GET /jobs/{id}."""
    dv_id = await seed_detector_version()
    train_ds = await seed_dataset(name="up-tr")
    test_ds = await seed_dataset(name="up-te")
    user_params = {"n_estimators": 200, "max_depth": 10}

    submit = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": user_params,
        },
    )
    assert submit.status_code == 202, submit.text
    job_id = submit.json()["id"]

    detail = await user_client.get(f"/api/v1/jobs/{job_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["user_params"] == user_params


# ---------------------------------------------------------------------------
# Phase 13b Q1 — resolve_detector_defaults helper unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "manifest, job_type, expected",
    [
        pytest.param(None, JobType.TRAIN, None, id="manifest_is_None"),
        pytest.param({"stages": {}}, JobType.TRAIN, None, id="stage_missing"),
        pytest.param(
            {"stages": {"train": {}}},
            JobType.TRAIN,
            None,
            id="params_schema_missing",
        ),
        pytest.param(
            {"stages": {"train": {"params_schema": {}}}},
            JobType.TRAIN,
            None,
            id="properties_missing",
        ),
        pytest.param(
            {"stages": {"train": {"params_schema": {"properties": {}}}}},
            JobType.TRAIN,
            None,
            id="properties_empty",
        ),
        pytest.param(
            {
                "stages": {
                    "train": {"params_schema": {"properties": {"foo": "not-a-dict"}}}
                }
            },
            JobType.TRAIN,
            None,
            id="property_value_non_dict_skipped",
        ),
        pytest.param(
            {
                "stages": {
                    "train": {
                        "params_schema": {
                            "properties": {
                                "with": {"type": "integer", "default": 5},
                                "without": {"type": "integer"},
                            }
                        }
                    }
                }
            },
            JobType.TRAIN,
            {"with": 5},
            id="field_without_default_excluded",
        ),
        pytest.param(
            {
                "stages": {
                    "train": {
                        "params_schema": {
                            "properties": {
                                "max_depth": {
                                    "type": ["integer", "null"],
                                    "default": None,
                                }
                            }
                        }
                    }
                }
            },
            JobType.TRAIN,
            {"max_depth": None},
            id="default_none_preserved",
        ),
        pytest.param(
            {
                "stages": {
                    "train": {
                        "params_schema": {
                            "properties": {
                                "zero": {"type": "integer", "default": 0},
                                "false_": {"type": "boolean", "default": False},
                                "empty": {"type": "string", "default": ""},
                            }
                        }
                    }
                }
            },
            JobType.TRAIN,
            {"zero": 0, "false_": False, "empty": ""},
            id="falsy_literals_preserved",
        ),
        pytest.param(
            {
                "stages": {
                    "train": {
                        "params_schema": {
                            "properties": {
                                "list_": {"type": "array", "default": []},
                                "obj": {"type": "object", "default": {}},
                            }
                        }
                    }
                }
            },
            JobType.TRAIN,
            {"list_": [], "obj": {}},
            id="collection_literals_preserved",
        ),
        pytest.param(
            {
                "stages": {
                    "train": {
                        "params_schema": {
                            "properties": {
                                "a": {"type": "integer", "default": 1},
                                "b": {"type": "integer"},  # no default
                                "c": {"type": "integer", "default": 3},
                            }
                        }
                    }
                }
            },
            JobType.TRAIN,
            {"a": 1, "c": 3},
            id="mix_with_and_without_defaults",
        ),
    ],
)
def test_resolve_detector_defaults(
    manifest: dict | None, job_type: JobType, expected: dict | None
) -> None:
    """Direct unit coverage for the helper, exercising every branch the HTTP
    round-trip tests below can't reach individually (missing keys, malformed
    property values, falsy literals)."""
    assert resolve_detector_defaults(manifest, job_type) == expected


# ---------------------------------------------------------------------------
# Phase 13b Q1 — detector_defaults round-trip on JobRead
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_returns_detector_defaults(
    user_client, seed_detector_version, seed_dataset
) -> None:
    """Happy path: a manifest whose train stage declares per-field defaults
    surfaces those defaults verbatim on ``JobRead.detector_defaults`` from
    ``GET /jobs/{id}``. Cross-check that ``user_params`` (the existing B3
    field) still round-trips alongside it.

    Includes ``max_depth: None`` to verify the helper preserves the literal
    null default — the override-indicator UI relies on this to distinguish
    a sklearn ``None`` default from "no default declared"."""
    dv_id = await seed_detector_version(
        name="rfdet-defaults", manifest=RICH_MANIFEST_WITH_TRAIN_DEFAULTS
    )
    train_ds = await seed_dataset(name="dd-tr")
    test_ds = await seed_dataset(name="dd-te")

    submit = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {"n_estimators": 200},
        },
    )
    assert submit.status_code == 202, submit.text
    job_id = submit.json()["id"]

    detail = await user_client.get(f"/api/v1/jobs/{job_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["detector_defaults"] == {
        "n_estimators": 100,
        "max_depth": None,
        "random_state": 42,
    }
    assert body["user_params"] == {"n_estimators": 200}


@pytest.mark.asyncio
async def test_get_job_detector_defaults_none_when_no_defaults_in_schema(
    user_client, seed_detector_version, seed_dataset
) -> None:
    """A manifest stage whose ``params_schema`` declares no ``default`` keys
    must surface ``detector_defaults`` as JSON ``null`` (not ``{}``). The
    frontend distinguishes the two: ``null`` hides the override-indicator
    column entirely, ``{}`` would render it with every row marked as an
    override. ``_MINIMAL_MANIFEST`` (the fixture default) has
    ``params_schema = {"type": "object"}`` with no ``properties``, so it's
    the natural fit for this assertion."""
    dv_id = await seed_detector_version()
    train_ds = await seed_dataset(name="nd-tr")
    test_ds = await seed_dataset(name="nd-te")

    submit = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert submit.status_code == 202, submit.text
    job_id = submit.json()["id"]

    detail = await user_client.get(f"/api/v1/jobs/{job_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["detector_defaults"] is None


@pytest.mark.asyncio
async def test_submit_job_returns_detector_defaults(
    user_client, seed_detector_version, seed_dataset
) -> None:
    """The 202 ``JobRead`` body from ``POST /jobs`` must already carry
    ``detector_defaults`` — the frontend uses this to render the
    override-indicator immediately after submission without a follow-up GET."""
    dv_id = await seed_detector_version(
        name="rfdet-submit", manifest=RICH_MANIFEST_WITH_TRAIN_DEFAULTS
    )
    train_ds = await seed_dataset(name="ds-tr")
    test_ds = await seed_dataset(name="ds-te")

    submit = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {"n_estimators": 250, "max_depth": 7},
        },
    )
    assert submit.status_code == 202, submit.text
    body = submit.json()
    assert body["detector_defaults"] == {
        "n_estimators": 100,
        "max_depth": None,
        "random_state": 42,
    }
    assert body["user_params"] == {"n_estimators": 250, "max_depth": 7}


@pytest.mark.asyncio
async def test_cancel_job_returns_detector_defaults(
    user_client, seed_detector_version, seed_dataset
) -> None:
    """``POST /jobs/{id}/cancel`` returns a ``JobRead`` and must carry the
    same ``detector_defaults`` payload the submit + GET endpoints do. Locks
    in the wiring on the third call site so a future refactor doesn't drop
    the field on cancel responses (the helper centralizes attachment, but
    the integration test guards against the wiring itself being skipped).

    ``cancel_job`` only succeeds while the job is non-terminal. After submit
    the row is in ``queued_backend`` status (Phase 6 Task E: dispatch is
    deferred to the fifo_scheduler reconciler), which is in
    ``NON_TERMINAL_STATUSES``."""
    dv_id = await seed_detector_version(
        name="rfdet-cancel", manifest=RICH_MANIFEST_WITH_TRAIN_DEFAULTS
    )
    train_ds = await seed_dataset(name="cd-tr")
    test_ds = await seed_dataset(name="cd-te")

    submit = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {"n_estimators": 300},
        },
    )
    assert submit.status_code == 202, submit.text
    job_id = submit.json()["id"]
    expected_defaults = submit.json()["detector_defaults"]

    cancel = await user_client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert cancel.status_code == 200, cancel.text
    body = cancel.json()
    assert body["status"] == "cancelled"
    assert body["detector_defaults"] == expected_defaults
    assert body["detector_defaults"] == {
        "n_estimators": 100,
        "max_depth": None,
        "random_state": 42,
    }


# ---------------------------------------------------------------------------
# Cutover v0.16.1 — JobRead.positive_class round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_returns_positive_class(
    user_client, seed_detector_version, seed_dataset
) -> None:
    """``JobRead.positive_class`` mirrors ``manifest.output.positive_class`` so
    the frontend can tag the positive row in PerClassMetrics and bias
    PredictionSummaryCard ordering without a follow-up call to fetch the
    detector_version manifest. The seeded ``_MINIMAL_MANIFEST`` declares
    ``Malware`` as the positive class."""
    dv_id = await seed_detector_version(name="rfdet-positive")
    train_ds = await seed_dataset(name="pc-tr")
    test_ds = await seed_dataset(name="pc-te")

    submit = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert submit.status_code == 202, submit.text
    job_id = submit.json()["id"]
    assert submit.json()["positive_class"] == "Malware"

    detail = await user_client.get(f"/api/v1/jobs/{job_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["positive_class"] == "Malware"


# ---------------------------------------------------------------------------
# Phase 2.4 — BACKEND_MAINTENANCE_MODE flag gates POST /api/v1/jobs.
# During the Phase 4 cutover (maldet 2.0 deps bumped → all detector data
# wiped → all detector images rebuilt) the operator flips this flag on so
# in-flight submissions don't write into a half-cleared MLflow / Job state.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_blocked_in_maintenance_mode(
    user_client, seed_detector_version, seed_dataset, monkeypatch
) -> None:
    """When ``BACKEND_MAINTENANCE_MODE=True``, ``POST /api/v1/jobs`` short
    circuits with HTTP 503 + ``Retry-After`` before doing any DB / MLflow
    work. The 503 detail string includes "maintenance" so the frontend can
    render a user-friendly banner."""
    from app.config import settings

    monkeypatch.setattr(settings, "BACKEND_MAINTENANCE_MODE", True)

    dv_id = await seed_detector_version(name="mm-on")
    train_ds = await seed_dataset(name="mm-on-tr")
    test_ds = await seed_dataset(name="mm-on-te")

    resp = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert resp.status_code == 503, resp.text
    assert resp.headers.get("Retry-After") is not None
    detail = resp.json().get("detail", "")
    assert "maintenance" in detail.lower()


@pytest.mark.asyncio
async def test_create_job_allowed_when_maintenance_off(
    user_client, seed_detector_version, seed_dataset, monkeypatch
) -> None:
    """When ``BACKEND_MAINTENANCE_MODE=False`` (default), submissions are
    allowed — the gate must never produce 503 with the flag off, even if
    other validation later returns 4xx. The path through the rest of the
    handler is exercised by the surrounding tests; here we just guard
    against the gate firing on a falsy flag."""
    from app.config import settings

    monkeypatch.setattr(settings, "BACKEND_MAINTENANCE_MODE", False)

    dv_id = await seed_detector_version(name="mm-off")
    train_ds = await seed_dataset(name="mm-off-tr")
    test_ds = await seed_dataset(name="mm-off-te")

    resp = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert resp.status_code != 503, resp.text
    assert resp.status_code in (200, 201, 202, 400, 422), resp.text
