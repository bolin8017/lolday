from pathlib import Path

import pytest
from httpx import AsyncClient

FIXTURE_CSV = (Path(__file__).parent / "fixtures" / "sample_dataset.csv").read_text()


@pytest.mark.asyncio
async def test_create_dataset_happy_path(user_client: AsyncClient):
    r = await user_client.post(
        "/api/v1/datasets",
        json={
            "name": "test-upx-ds",
            "description": "7-row fixture",
            "visibility": "public",
            "csv_content": FIXTURE_CSV,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sample_count"] == 7
    assert body["label_distribution"] == {"Malware": 5, "Benign": 2}
    assert body["csv_checksum"]
    assert "csv_content" not in body


@pytest.mark.asyncio
async def test_create_dataset_rejects_oversize(user_client: AsyncClient, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATASET_CSV_MAX_BYTES", 10)
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "big", "csv_content": FIXTURE_CSV},
    )
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_create_dataset_rejects_malformed_csv(user_client: AsyncClient):
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "bad", "csv_content": "not,a,valid,csv\n"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_dataset_duplicate_name_rejected(user_client: AsyncClient):
    await user_client.post(
        "/api/v1/datasets",
        json={"name": "dup", "csv_content": FIXTURE_CSV},
    )
    r = await user_client.post(
        "/api/v1/datasets",
        json={"name": "dup", "csv_content": FIXTURE_CSV},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_list_datasets_paginated(user_client: AsyncClient):
    for i in range(3):
        await user_client.post(
            "/api/v1/datasets",
            json={"name": f"d{i}", "csv_content": FIXTURE_CSV},
        )
    r = await user_client.get("/api/v1/datasets?page=1&page_size=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_get_dataset_returns_metadata_not_content(user_client: AsyncClient):
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "foo", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]
    r = await user_client.get(f"/api/v1/datasets/{ds_id}")
    assert r.status_code == 200
    assert "csv_content" not in r.json()


@pytest.mark.asyncio
async def test_get_dataset_csv_returns_raw_content(user_client: AsyncClient):
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "foo", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]
    r = await user_client.get(f"/api/v1/datasets/{ds_id}/csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text == FIXTURE_CSV


@pytest.mark.asyncio
async def test_private_dataset_hidden_from_other_user(
    user_client: AsyncClient, second_user_client: AsyncClient
):
    r1 = await user_client.post(
        "/api/v1/datasets",
        json={"name": "secret", "visibility": "private", "csv_content": FIXTURE_CSV},
    )
    ds_id = r1.json()["id"]

    r2 = await second_user_client.get(f"/api/v1/datasets/{ds_id}")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_patch_dataset_only_allowed_fields(user_client: AsyncClient):
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "foo", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]

    r = await user_client.patch(
        f"/api/v1/datasets/{ds_id}",
        json={"description": "new desc"},
    )
    assert r.status_code == 200
    assert r.json()["description"] == "new desc"

    await user_client.patch(f"/api/v1/datasets/{ds_id}", json={"csv_content": "x"})
    get_r = await user_client.get(f"/api/v1/datasets/{ds_id}/csv")
    assert get_r.text == FIXTURE_CSV


@pytest.mark.asyncio
async def test_clone_dataset_makes_copy_owned_by_caller(
    user_client: AsyncClient, second_user_client: AsyncClient
):
    r1 = await user_client.post(
        "/api/v1/datasets",
        json={"name": "orig", "csv_content": FIXTURE_CSV},
    )
    orig_id = r1.json()["id"]
    orig_owner = r1.json()["owner_id"]

    r2 = await second_user_client.post(f"/api/v1/datasets/{orig_id}/clone")
    assert r2.status_code == 201
    body = r2.json()
    assert body["owner_id"] != orig_owner
    assert body["name"].endswith("-clone")
    assert body["csv_checksum"] == r1.json()["csv_checksum"]


@pytest.mark.asyncio
async def test_delete_dataset_soft_deletes(user_client: AsyncClient):
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "to-del", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]

    r = await user_client.delete(f"/api/v1/datasets/{ds_id}")
    assert r.status_code == 204

    r2 = await user_client.get(f"/api/v1/datasets/{ds_id}")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_create_dataset_rejects_crlf_in_name(user_client: AsyncClient):
    r = await user_client.post(
        "/api/v1/datasets",
        json={
            "name": "evil\r\nContent-Type: text/html",
            "csv_content": FIXTURE_CSV,
        },
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_create_dataset_rejects_quote_in_name(user_client: AsyncClient):
    r = await user_client.post(
        "/api/v1/datasets",
        json={
            "name": 'a"b',
            "csv_content": FIXTURE_CSV,
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_dataset_accepts_unicode_dash_and_dot(user_client: AsyncClient):
    r = await user_client.post(
        "/api/v1/datasets",
        json={
            "name": "ds.test_v1-2",
            "csv_content": FIXTURE_CSV,
        },
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_csv_download_uses_rfc6266_header(user_client: AsyncClient):
    # Create with a unicode-bearing name; the regex from H-6a allows ASCII
    # only, so the unicode case is purely for the encoding helper.
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "ds-test", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]
    r = await user_client.get(f"/api/v1/datasets/{ds_id}/csv")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # Dual-form: ASCII fallback + RFC 5987 percent-encoded UTF-8.
    assert cd.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in cd


@pytest.mark.asyncio
async def test_delete_dataset_blocked_by_active_job(
    user_client, seed_detector_version, seed_dataset
):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {},
        },
    )
    assert r.status_code == 202

    r = await user_client.delete(f"/api/v1/datasets/{tr}")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_list_datasets_escapes_percent_wildcard(user_client: AsyncClient):
    # Create two datasets with unrelated names.
    for n in ("alpha-one", "beta-two"):
        r = await user_client.post(
            "/api/v1/datasets",
            json={"name": n, "csv_content": FIXTURE_CSV},
        )
        assert r.status_code == 201
    # `%` should be treated as a literal char, matching neither.
    r = await user_client.get("/api/v1/datasets?search=%25")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0, body


@pytest.mark.asyncio
async def test_clone_of_private_dataset_stays_private(
    user_client: AsyncClient, auth_client_admin: AsyncClient
):
    # admin creates a PRIVATE dataset
    create = await auth_client_admin.post(
        "/api/v1/datasets",
        json={
            "name": "secret",
            "visibility": "private",
            "csv_content": FIXTURE_CSV,
        },
    )
    assert create.status_code == 201, create.text
    ds_id = create.json()["id"]
    # admin clones their own PRIVATE dataset; the clone must inherit visibility
    r = await auth_client_admin.post(f"/api/v1/datasets/{ds_id}/clone")
    assert r.status_code == 201, r.text
    assert r.json()["visibility"] == "private"


@pytest.mark.asyncio
async def test_clone_of_public_dataset_stays_public(user_client: AsyncClient):
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "shared", "visibility": "public", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]
    r = await user_client.post(f"/api/v1/datasets/{ds_id}/clone")
    assert r.status_code == 201
    assert r.json()["visibility"] == "public"
