from app.models.detector import Detector, DetectorBuild, DetectorVersion
from app.models.credential import UserGitCredential


def test_detector_has_required_fields():
    cols = {c.name for c in Detector.__table__.columns}
    assert cols >= {
        "id", "name", "display_name", "description", "git_url",
        "owner_id", "created_at", "deleted_at",
    }


def test_detector_version_has_required_fields():
    cols = {c.name for c in DetectorVersion.__table__.columns}
    assert cols >= {
        "id", "detector_id", "git_tag", "git_sha", "harbor_image",
        "image_digest", "config_schema", "built_at", "status",
    }


def test_detector_build_has_required_fields():
    cols = {c.name for c in DetectorBuild.__table__.columns}
    assert cols >= {
        "id", "detector_id", "git_tag", "git_sha", "triggered_by_id",
        "k8s_job_name", "status", "failure_reason", "log_tail",
        "trivy_critical", "trivy_high", "started_at", "finished_at",
    }


def test_detector_build_no_pending_schema_column() -> None:
    """Phase 11c: pending_schema column dropped (was v0 schema POST landing)."""
    assert "pending_schema" not in DetectorBuild.__table__.columns


def test_user_git_credential_has_required_fields():
    cols = {c.name for c in UserGitCredential.__table__.columns}
    assert cols >= {
        "user_id", "provider", "encrypted_token", "token_hint",
        "created_at", "updated_at",
    }
