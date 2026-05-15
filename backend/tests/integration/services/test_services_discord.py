"""Tests for app.services.discord — Discord webhook embed builders."""

from app.services.discord import (
    COLOR_FAIL,
    COLOR_SUCCESS,
    COLOR_WARN,
    build_build_completed_embed,
    build_build_failed_embed,
    build_job_completed_embed,
    build_job_failed_embed,
    build_trivy_blocked_embed,
)


def _fields_by_name(embed_payload: dict) -> dict[str, str]:
    return {f["name"]: f["value"] for f in embed_payload["embeds"][0]["fields"]}


def test_job_completed_embed_uses_green_and_success_title():
    payload = build_job_completed_embed(
        user_name="alice",
        user_discord_id=None,
        job_type="train",
        detector_label="upxelfdet v0.5.0",
        dataset_name="UPX-ELF-balanced",
        duration_seconds=42,
        primary_metric=("f1", 0.94),
        job_url="https://lolday.connlabai.com/jobs/xyz",
        mlflow_url=None,
    )
    assert payload["embeds"][0]["color"] == COLOR_SUCCESS
    assert "Job train completed" in payload["embeds"][0]["title"]
    assert payload["embeds"][0]["url"] == "https://lolday.connlabai.com/jobs/xyz"


def test_job_completed_embed_prefixes_plain_user_when_no_discord_id():
    payload = build_job_completed_embed(
        user_name="alice",
        user_discord_id=None,
        job_type="train",
        detector_label="d",
        dataset_name=None,
        duration_seconds=None,
        primary_metric=None,
        job_url="u",
        mlflow_url=None,
    )
    assert payload["content"] == "**@alice**"


def test_job_completed_embed_pings_discord_id_when_present():
    payload = build_job_completed_embed(
        user_name="alice",
        user_discord_id="123456789012345678",
        job_type="train",
        detector_label="d",
        dataset_name=None,
        duration_seconds=None,
        primary_metric=None,
        job_url="u",
        mlflow_url=None,
    )
    assert payload["content"] == "<@123456789012345678>"


def test_job_completed_embed_includes_metric_and_duration_fields():
    payload = build_job_completed_embed(
        user_name="alice",
        user_discord_id=None,
        job_type="train",
        detector_label="upxelfdet v0.5.0",
        dataset_name="UPX-ELF-balanced",
        duration_seconds=42,
        primary_metric=("f1", 0.94),
        job_url="u",
        mlflow_url=None,
    )
    fields = _fields_by_name(payload)
    assert fields["Detector"] == "upxelfdet v0.5.0"
    assert fields["Dataset"] == "UPX-ELF-balanced"
    assert fields["Duration"] == "42s"
    assert fields["f1"] == "0.9400"


def test_job_completed_embed_omits_optional_fields_when_missing():
    payload = build_job_completed_embed(
        user_name="alice",
        user_discord_id=None,
        job_type="train",
        detector_label="d",
        dataset_name=None,
        duration_seconds=None,
        primary_metric=None,
        job_url="u",
        mlflow_url=None,
    )
    fields = _fields_by_name(payload)
    assert "Dataset" not in fields
    assert "Duration" not in fields
    assert "Detector" in fields  # required


def test_job_completed_embed_includes_mlflow_markdown_link_when_provided():
    payload = build_job_completed_embed(
        user_name="alice",
        user_discord_id=None,
        job_type="train",
        detector_label="d",
        dataset_name=None,
        duration_seconds=None,
        primary_metric=None,
        job_url="u",
        mlflow_url="https://lolday.connlabai.com/runs/e/r",
    )
    fields = _fields_by_name(payload)
    assert fields["MLflow"] == "[Open run](https://lolday.connlabai.com/runs/e/r)"


def test_job_failed_embed_uses_red_and_includes_failure_reason():
    payload = build_job_failed_embed(
        user_name="alice",
        user_discord_id=None,
        job_type="train",
        detector_label="d",
        dataset_name="ds",
        failure_reason="OOM killed by k8s",
        job_url="https://lolday.connlabai.com/jobs/xyz",
    )
    assert payload["embeds"][0]["color"] == COLOR_FAIL
    assert "Job train failed" in payload["embeds"][0]["title"]
    fields = _fields_by_name(payload)
    assert fields["Failure reason"] == "OOM killed by k8s"


def test_build_completed_embed_green_with_commit_sha():
    payload = build_build_completed_embed(
        user_name="bob",
        user_discord_id="99",
        detector_label="upxelfdet",
        git_tag="v0.5.0",
        commit_sha="deadbeef1234567890",
        build_url="https://lolday.connlabai.com/detectors/xxx",
    )
    assert payload["embeds"][0]["color"] == COLOR_SUCCESS
    assert payload["content"] == "<@99>"
    fields = _fields_by_name(payload)
    assert fields["Git tag"] == "v0.5.0"
    assert fields["Commit"] == "deadbee"  # short sha (7 chars)


def test_build_failed_embed_red_with_reason():
    payload = build_build_failed_embed(
        user_name="bob",
        user_discord_id=None,
        detector_label="upxelfdet",
        git_tag="v0.5.0",
        failure_reason="kaniko: non-zero exit",
        build_url="u",
    )
    assert payload["embeds"][0]["color"] == COLOR_FAIL
    fields = _fields_by_name(payload)
    assert fields["Failure reason"] == "kaniko: non-zero exit"


def test_trivy_blocked_embed_orange_with_cve_summary():
    payload = build_trivy_blocked_embed(
        user_name="bob",
        user_discord_id=None,
        detector_label="upxelfdet",
        git_tag="v0.5.0",
        cve_summary="5 critical, 12 high",
        build_url="u",
    )
    assert payload["embeds"][0]["color"] == COLOR_WARN
    assert "Trivy blocked" in payload["embeds"][0]["title"]
    fields = _fields_by_name(payload)
    assert fields["CVEs"] == "5 critical, 12 high"
