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
from app.routers.jobs import _strategy_from_manifest
from maldet.manifest import DetectorManifest

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
            "classes": ["Malware", "Benign"],
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
    assert _strategy_from_manifest(m) == strategy


def test_strategy_from_manifest_unknown_strategy_raises() -> None:
    """Pydantic constrains the literal at schema time, but if the API ever
    accepts a richer set later, ``_strategy_from_manifest`` must still be
    the source of truth on the platform side. Construct the model with a
    bypass to assert the helper rejects unknown values rather than passing
    them through as opaque env values."""

    # Build a manifest with a valid value, then mutate the underlying field
    # to bypass pydantic's Literal[...] check. This is the only realistic
    # way an unknown string could reach the helper at runtime — a stored
    # manifest from a future schema or a corrupted DB row.
    m = _manifest("ddp")
    object.__setattr__(m.lifecycle, "supports_distributed", "horovod")

    with pytest.raises(ValueError, match="not a known strategy"):
        _strategy_from_manifest(m)


@pytest.mark.parametrize("bool_val", [True, False])
def test_strategy_from_manifest_bool_falls_back_to_ddp(bool_val: bool) -> None:
    """The boolean form is the legacy / opt-out shape from
    ``maldet < 1.1``. Lightning ignores ``strategy=ddp`` when GPU count <= 1,
    so falling back to ``"ddp"`` is safe regardless of resource_profile."""
    m = _manifest(bool_val)
    assert _strategy_from_manifest(m) == "ddp"


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
            "classes": ["Malware", "Benign"],
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
# Phase 13b Q1 — detector_defaults round-trip on JobRead
# ---------------------------------------------------------------------------


def _rich_manifest_with_train_defaults() -> dict:
    """Manifest mirroring the elfrfdet shape: a train stage whose
    ``params_schema.properties`` declares ``default`` for each field.

    Used by the detector_defaults tests below. ``max_depth`` carries
    ``default: None`` deliberately — the helper must distinguish
    "default declared as null" from "no default declared", so the test
    asserts ``max_depth: None`` survives the round-trip."""
    return {
        "detector": {"name": "rfdet", "version": "0.1.0", "framework": "sklearn"},
        "input": {
            "binary_format": "elf",
            "required_sections": [],
            "dataset_contract": "sample_csv",
        },
        "output": {
            "task": "binary_classification",
            "classes": ["Malware", "Benign"],
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
            "train": {
                "config_class": "test.configs:TrainConfig",
                "params_schema": {
                    "type": "object",
                    "properties": {
                        "n_estimators": {"type": "integer", "default": 100},
                        "max_depth": {
                            "type": ["integer", "null"],
                            "default": None,
                        },
                        "random_state": {"type": "integer", "default": 42},
                    },
                },
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


async def _seed_detector_version_with_manifest(
    db_session, seed_user, manifest: dict, name: str
) -> str:
    """Insert a DetectorVersion with an arbitrary manifest dict, return its id.

    The packaged ``seed_detector_version`` fixture pins ``_MINIMAL_MANIFEST``,
    whose ``params_schema`` is empty — these tests need richer schemas to
    exercise the defaults extraction. Rather than parametrise the existing
    fixture, we duplicate the row-insert here for clarity at the call site.
    """
    from uuid import uuid4

    from app.models import Detector, DetectorVersion
    from app.models.detector import DetectorVersionStatus

    det = Detector(
        name=f"{name}-{uuid4().hex[:6]}",
        display_name=name,
        git_url=f"https://github.com/test/{name}.git",
        owner_id=seed_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v0.1.0",
        git_sha="a" * 40,
        harbor_image=f"harbor.harbor.svc:80/detectors/{det.name}:v0.1.0",
        image_digest="sha256:" + "a" * 64,
        status=DetectorVersionStatus.ACTIVE,
        manifest=manifest,
    )
    db_session.add(dv)
    await db_session.commit()
    return str(dv.id)


@pytest.mark.asyncio
async def test_get_job_returns_detector_defaults(
    user_client, db_session, seed_user, seed_dataset
) -> None:
    """Happy path: a manifest whose train stage declares per-field defaults
    surfaces those defaults verbatim on ``JobRead.detector_defaults`` from
    ``GET /jobs/{id}``. Cross-check that ``user_params`` (the existing B3
    field) still round-trips alongside it.

    Includes ``max_depth: None`` to verify the helper preserves the literal
    null default — the override-indicator UI relies on this to distinguish
    a sklearn ``None`` default from "no default declared"."""
    dv_id = await _seed_detector_version_with_manifest(
        db_session,
        seed_user,
        _rich_manifest_with_train_defaults(),
        name="rfdet-defaults",
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
    override. ``_MINIMAL_MANIFEST`` (used by ``seed_detector_version``) has
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
    user_client, db_session, seed_user, seed_dataset
) -> None:
    """The 202 ``JobRead`` body from ``POST /jobs`` must already carry
    ``detector_defaults`` — the frontend uses this to render the
    override-indicator immediately after submission without a follow-up GET."""
    dv_id = await _seed_detector_version_with_manifest(
        db_session,
        seed_user,
        _rich_manifest_with_train_defaults(),
        name="rfdet-submit",
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
