"""MLflow naming alignment with v0.20.0 owner/detector namespace.

Aligns experiment + run-tag naming with the model-registry namespace adopted in
v0.20.0 (`{owner_handle}/{detector_name}`). Before this refactor, exp names were
`detector:{detector_uuid}:{git_tag}` and key run tags carried bare UUIDs as
values. After the refactor:

- experiment name: `{owner_handle}/{detector_name}/{git_tag}`
- run name: `{action}-{job_short_id}` (job_short_id = first 8 hex chars of Job UUID)
- run tag `lolday.user`: handle (human-readable)
- run tag `lolday.user_id`: UUID (programmatic search)
- run tag `lolday.detector_version`: `{detector_name}/{git_tag}` (human-readable)
- run tag `lolday.detector_version_id`: UUID (programmatic search)
"""

import re

import pytest


def _tags_to_dict(tags: list[dict[str, str]]) -> dict[str, str]:
    return {t["key"]: t["value"] for t in tags}


@pytest.mark.asyncio
async def test_experiment_name_uses_owner_detector_tag_namespace(
    user_client, seed_detector_version, seed_dataset, seed_user, mock_mlflow
):
    dv_id = await seed_detector_version(name="elf-rf", git_tag="v4.0.0")
    train_ds = await seed_dataset(name="tr-ds")
    test_ds = await seed_dataset(name="te-ds")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {"seed": 42},
        },
    )
    assert r.status_code == 202, r.text

    expected = f"{seed_user.handle}/elf-rf/v4.0.0"
    assert expected in mock_mlflow.experiment_creates, (
        f"expected experiment name {expected!r} in {mock_mlflow.experiment_creates!r}"
    )


@pytest.mark.asyncio
async def test_run_tags_use_human_readable_values_with_id_companions(
    user_client, seed_detector_version, seed_dataset, seed_user, mock_mlflow
):
    dv_id = await seed_detector_version(name="elf-cnn", git_tag="v4.0.0")
    train_ds = await seed_dataset(name="tr-ds")
    test_ds = await seed_dataset(name="te-ds")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert r.status_code == 202, r.text

    assert mock_mlflow.runs_created, "no runs created"
    _, tags = mock_mlflow.runs_created[-1]
    tag_dict = _tags_to_dict(tags)

    assert tag_dict["maldet.action"] == "train"
    assert tag_dict["lolday.user"] == seed_user.handle
    assert tag_dict["lolday.user_id"] == str(seed_user.id)
    assert tag_dict["lolday.detector_version"] == "elf-cnn/v4.0.0"
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        tag_dict["lolday.detector_version_id"],
    ), f"not a UUID: {tag_dict['lolday.detector_version_id']!r}"


@pytest.mark.asyncio
async def test_run_name_is_action_plus_job_short_id(
    user_client, seed_detector_version, seed_dataset, mock_mlflow
):
    dv_id = await seed_detector_version(name="elf-rf", git_tag="v4.0.0")
    train_ds = await seed_dataset(name="tr-ds")
    test_ds = await seed_dataset(name="te-ds")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    expected_run_name = f"train-{job_id.replace('-', '')[:8]}"

    _, tags = mock_mlflow.runs_created[-1]
    tag_dict = _tags_to_dict(tags)
    assert tag_dict["mlflow.runName"] == expected_run_name


@pytest.mark.asyncio
async def test_legacy_uuid_pattern_no_longer_used(
    user_client, seed_detector_version, seed_dataset, mock_mlflow
):
    dv_id = await seed_detector_version(name="elf-rf", git_tag="v4.0.0")
    train_ds = await seed_dataset(name="tr-ds")
    test_ds = await seed_dataset(name="te-ds")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert r.status_code == 202

    for name in mock_mlflow.experiment_creates:
        assert not name.startswith("detector:"), (
            f"legacy `detector:UUID:tag` pattern resurfaced: {name!r}"
        )

    _, tags = mock_mlflow.runs_created[-1]
    tag_dict = _tags_to_dict(tags)
    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    assert not uuid_re.fullmatch(tag_dict["lolday.user"])
    assert not uuid_re.fullmatch(tag_dict["lolday.detector_version"])
