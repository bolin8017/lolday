"""GET /internal/jobs/{id}/config — sidecar fetches resolved yaml + dataset CSVs.

Companion to test_internal_events.py; that file covered POST events, this
file covers the read-side internal endpoint (zero coverage before).
"""

from __future__ import annotations

import uuid

import pytest
from app.models import (
    DatasetConfig,
    DatasetVisibility,
    Detector,
    DetectorVersion,
    Job,
    User,
)
from app.models.job import JobStatus
from app.services.job_tokens import generate_token, hash_token
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_job(
    session: AsyncSession,
    *,
    resolved_config: dict | None = None,
    train_dataset_id: uuid.UUID | None = None,
    test_dataset_id: uuid.UUID | None = None,
    predict_dataset_id: uuid.UUID | None = None,
) -> tuple[Job, str]:
    uid = uuid.uuid4()
    user = User(
        id=uid,
        email=f"int-cfg-{uid.hex[:8]}@example.com",
        handle=f"int-cfg-{uid.hex[:8]}",
    )
    session.add(user)
    await session.flush()
    det = Detector(
        name=f"int-cfg-{uuid.uuid4().hex[:8]}",
        display_name="int-cfg",
        owner_id=user.id,
        git_url="https://example.com/r.git",
    )
    session.add(det)
    await session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v1",
        git_sha="deadbeef",
        harbor_image="h/x:v1",
        image_digest="sha256:abc",
    )
    session.add(dv)
    await session.flush()
    raw_token = generate_token()
    job = Job(
        type="train",
        status=JobStatus.RUNNING,
        owner_id=user.id,
        detector_version_id=dv.id,
        resolved_config=resolved_config if resolved_config is not None else {},
        idempotency_key=uuid.uuid4().hex,
        token_hash=hash_token(raw_token),
        train_dataset_id=train_dataset_id,
        test_dataset_id=test_dataset_id,
        predict_dataset_id=predict_dataset_id,
    )
    session.add(job)
    await session.commit()
    return job, raw_token


async def _seed_dataset(
    session: AsyncSession, owner_id: uuid.UUID, *, csv: str
) -> DatasetConfig:
    ds = DatasetConfig(
        name=f"ds-{uuid.uuid4().hex[:8]}",
        owner_id=owner_id,
        visibility=DatasetVisibility.PUBLIC,
        csv_content=csv,
        csv_checksum="0" * 64,
        sample_count=1,
        label_distribution={"benign": 1},
        size_bytes=len(csv),
    )
    session.add(ds)
    await session.commit()
    return ds


@pytest.mark.asyncio
async def test_get_config_happy_path_returns_yaml_and_csvs(
    db_session: AsyncSession, internal_client: AsyncClient
) -> None:
    """All three dataset slots populated; yaml carried in resolved_config."""
    job, raw_token = await _seed_job(
        db_session, resolved_config={"yaml": "epochs: 3\nbatch_size: 32"}
    )
    train_ds = await _seed_dataset(
        db_session, job.owner_id, csv="sha,label\nabc,benign\n"
    )
    test_ds = await _seed_dataset(
        db_session, job.owner_id, csv="sha,label\ndef,malware\n"
    )
    predict_ds = await _seed_dataset(db_session, job.owner_id, csv="sha\nghi\n")

    job.train_dataset_id = train_ds.id
    job.test_dataset_id = test_ds.id
    job.predict_dataset_id = predict_ds.id
    db_session.add(job)
    await db_session.commit()

    resp = await internal_client.get(
        f"/api/v1/internal/jobs/{job.id}/config",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["yaml"] == "epochs: 3\nbatch_size: 32"
    assert body["train_csv"] == "sha,label\nabc,benign\n"
    assert body["test_csv"] == "sha,label\ndef,malware\n"
    assert body["predict_csv"] == "sha\nghi\n"


@pytest.mark.asyncio
async def test_get_config_returns_null_csvs_when_dataset_ids_unset(
    db_session: AsyncSession, internal_client: AsyncClient
) -> None:
    """A train-only job with no test/predict datasets returns null for those CSVs."""
    job, raw_token = await _seed_job(db_session, resolved_config={"yaml": "k: v"})

    resp = await internal_client.get(
        f"/api/v1/internal/jobs/{job.id}/config",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["yaml"] == "k: v"
    assert body["train_csv"] is None
    assert body["test_csv"] is None
    assert body["predict_csv"] is None


@pytest.mark.asyncio
async def test_get_config_yaml_empty_when_resolved_config_not_dict(
    db_session: AsyncSession, internal_client: AsyncClient
) -> None:
    """resolved_config is JSON, but per-row it can be non-dict (legacy / corrupt).
    The endpoint must degrade to yaml="" rather than 500."""
    # A list-shaped resolved_config triggers the `isinstance(..., dict)` False
    # branch in the router.
    job, raw_token = await _seed_job(db_session, resolved_config=["not", "a", "dict"])  # type: ignore[arg-type]

    resp = await internal_client.get(
        f"/api/v1/internal/jobs/{job.id}/config",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["yaml"] == ""


@pytest.mark.asyncio
async def test_get_config_yaml_empty_when_yaml_key_missing(
    db_session: AsyncSession, internal_client: AsyncClient
) -> None:
    """resolved_config is a dict but lacks the 'yaml' key — return "" not 500."""
    job, raw_token = await _seed_job(db_session, resolved_config={"other": "k"})

    resp = await internal_client.get(
        f"/api/v1/internal/jobs/{job.id}/config",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["yaml"] == ""


@pytest.mark.asyncio
async def test_get_config_rejects_invalid_token(
    db_session: AsyncSession, internal_client: AsyncClient
) -> None:
    job, _ = await _seed_job(db_session)
    resp = await internal_client.get(
        f"/api/v1/internal/jobs/{job.id}/config",
        headers={"Authorization": "Bearer bogus-token"},
    )
    assert resp.status_code in (401, 403, 404)
