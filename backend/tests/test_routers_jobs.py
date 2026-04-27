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
from maldet.manifest import DetectorManifest

from app.routers.jobs import _strategy_from_manifest


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
