"""Discord webhook payload builders for user-facing lolday events.

Each `build_*_embed` returns a JSON-serialisable dict shaped for the Discord
webhook REST API (POST <webhook>). The caller handles HTTP delivery.
"""

from __future__ import annotations

COLOR_SUCCESS = 0x2ECC71
COLOR_FAIL = 0xE74C3C
COLOR_WARN = 0xF39C12


def _content_prefix(user_name: str, user_discord_id: str | None) -> str:
    if user_discord_id:
        return f"<@{user_discord_id}>"
    return f"**@{user_name}**"


def _field(name: str, value: str, inline: bool = True) -> dict:
    return {"name": name, "value": value, "inline": inline}


def build_job_completed_embed(
    *,
    user_name: str,
    user_discord_id: str | None,
    job_type: str,
    detector_label: str,
    dataset_name: str | None,
    duration_seconds: int | None,
    primary_metric: tuple[str, float] | None,
    job_url: str,
    mlflow_url: str | None,
) -> dict:
    fields: list[dict] = [_field("Detector", detector_label)]
    if dataset_name:
        fields.append(_field("Dataset", dataset_name))
    if duration_seconds is not None:
        fields.append(_field("Duration", f"{duration_seconds}s"))
    if primary_metric is not None:
        metric_name, metric_value = primary_metric
        fields.append(_field(metric_name, f"{metric_value:.4f}"))
    if mlflow_url:
        fields.append(_field("MLflow", f"[Open run]({mlflow_url})", inline=False))
    return {
        "content": _content_prefix(user_name, user_discord_id),
        "embeds": [{
            "title": f"✅ Job {job_type} completed",
            "url": job_url,
            "color": COLOR_SUCCESS,
            "fields": fields,
        }],
    }


def build_job_failed_embed(
    *,
    user_name: str,
    user_discord_id: str | None,
    job_type: str,
    detector_label: str,
    dataset_name: str | None,
    failure_reason: str | None,
    job_url: str,
) -> dict:
    fields: list[dict] = [_field("Detector", detector_label)]
    if dataset_name:
        fields.append(_field("Dataset", dataset_name))
    fields.append(_field("Failure reason", failure_reason or "(unknown)", inline=False))
    return {
        "content": _content_prefix(user_name, user_discord_id),
        "embeds": [{
            "title": f"❌ Job {job_type} failed",
            "url": job_url,
            "color": COLOR_FAIL,
            "fields": fields,
        }],
    }


def build_build_completed_embed(
    *,
    user_name: str,
    user_discord_id: str | None,
    detector_label: str,
    git_tag: str,
    commit_sha: str,
    build_url: str,
) -> dict:
    return {
        "content": _content_prefix(user_name, user_discord_id),
        "embeds": [{
            "title": f"✅ Build completed — {detector_label}",
            "url": build_url,
            "color": COLOR_SUCCESS,
            "fields": [
                _field("Git tag", git_tag),
                _field("Commit", commit_sha[:7]),
            ],
        }],
    }


def build_build_failed_embed(
    *,
    user_name: str,
    user_discord_id: str | None,
    detector_label: str,
    git_tag: str,
    failure_reason: str | None,
    build_url: str,
) -> dict:
    return {
        "content": _content_prefix(user_name, user_discord_id),
        "embeds": [{
            "title": f"❌ Build failed — {detector_label}",
            "url": build_url,
            "color": COLOR_FAIL,
            "fields": [
                _field("Git tag", git_tag),
                _field("Failure reason", failure_reason or "(unknown)", inline=False),
            ],
        }],
    }


def build_trivy_blocked_embed(
    *,
    user_name: str,
    user_discord_id: str | None,
    detector_label: str,
    git_tag: str,
    cve_summary: str,
    build_url: str,
) -> dict:
    return {
        "content": _content_prefix(user_name, user_discord_id),
        "embeds": [{
            "title": f"⚠️ Trivy blocked — {detector_label}",
            "url": build_url,
            "color": COLOR_WARN,
            "fields": [
                _field("Git tag", git_tag),
                _field("CVEs", cve_summary),
            ],
        }],
    }
