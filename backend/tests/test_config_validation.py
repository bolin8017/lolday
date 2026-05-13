"""Regression guards for production-config Settings validators.

Two model_validators fail boot when ENVIRONMENT=production and the input is
unsafe for production:

- ``validate_sso_config`` (Phase 10): rejects AUTH_DEV_MODE=true and empty
  CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_APP_AUD; otherwise CF Access JWT
  verification silently 401s every request.
- ``validate_helper_images`` (helper-image-versioning, 2026-04-29): rejects
  empty BUILD_IMAGE_HELPER / JOB_HELPER_IMAGE; without these the build
  pipeline and vcjob spec render incomplete env to backend pods.

These tests catch a future refactor that relaxes either validator before
the misconfiguration ships to prod.
"""

import pytest
from pydantic import ValidationError


def test_settings_rejects_auth_dev_mode_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "x" * 64)

    from app.config import Settings

    with pytest.raises(ValidationError, match="AUTH_DEV_MODE=true is forbidden"):
        Settings()


def test_settings_rejects_empty_cf_team_domain_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "x" * 64)

    from app.config import Settings

    with pytest.raises(ValidationError, match="must both be set"):
        Settings()


def test_settings_rejects_empty_cf_app_aud_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "")

    from app.config import Settings

    with pytest.raises(ValidationError, match="must both be set"):
        Settings()


def test_settings_accepts_auth_dev_mode_outside_production(monkeypatch):
    """Local dev / CI uses AUTH_DEV_MODE=true — must not raise."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("AUTH_DEV_EMAIL", "dev@local")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "")

    from app.config import Settings

    s = Settings()
    assert s.AUTH_DEV_MODE is True
    assert s.ENVIRONMENT == "development"


def test_settings_rejects_empty_build_image_helper_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "x" * 64)
    monkeypatch.setenv("BUILD_IMAGE_HELPER", "")
    monkeypatch.setenv("JOB_HELPER_IMAGE", "harbor.lolday.svc:80/lolday/job-helper:abc")

    from app.config import Settings

    with pytest.raises(ValidationError, match="BUILD_IMAGE_HELPER must be set"):
        Settings()


def test_settings_rejects_empty_job_helper_image_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "x" * 64)
    monkeypatch.setenv(
        "BUILD_IMAGE_HELPER", "harbor.lolday.svc:80/lolday/build-helper:abc"
    )
    monkeypatch.setenv("JOB_HELPER_IMAGE", "")

    from app.config import Settings

    with pytest.raises(ValidationError, match="JOB_HELPER_IMAGE must be set"):
        Settings()


def test_settings_accepts_filled_helper_images_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "x" * 64)
    monkeypatch.setenv(
        "BUILD_IMAGE_HELPER", "harbor.lolday.svc:80/lolday/build-helper:abc"
    )
    monkeypatch.setenv("JOB_HELPER_IMAGE", "harbor.lolday.svc:80/lolday/job-helper:def")

    from app.config import Settings

    s = Settings()
    assert s.BUILD_IMAGE_HELPER.endswith(":abc")
    assert s.JOB_HELPER_IMAGE.endswith(":def")


def test_settings_accepts_empty_helper_images_outside_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BUILD_IMAGE_HELPER", "")
    monkeypatch.setenv("JOB_HELPER_IMAGE", "")

    from app.config import Settings

    s = Settings()
    assert s.BUILD_IMAGE_HELPER == ""
    assert s.JOB_HELPER_IMAGE == ""


def test_settings_rejects_both_empty_helper_images_in_production(monkeypatch):
    """Both empty in production must list both names in the error — exercises
    the ', '.join(missing) path that single-empty cases skip."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "x" * 64)
    monkeypatch.setenv("BUILD_IMAGE_HELPER", "")
    monkeypatch.setenv("JOB_HELPER_IMAGE", "")

    from app.config import Settings

    with pytest.raises(
        ValidationError, match="BUILD_IMAGE_HELPER, JOB_HELPER_IMAGE must be set"
    ):
        Settings()


def test_test_session_does_not_use_legacy_fernet_key():
    """H-17a: the conftest.py default for FERNET_KEY must NOT be the public
    test value that was hardcoded in the repo through 2026-05-13. Production
    defense lives in Settings.validate_fernet_keys (T8); this guard catches
    a future contributor who reverts conftest to a stable cleartext.
    """
    from app.config import settings

    LEGACY = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
    # Pre-T8 the field is FERNET_KEY (singular); post-T8 it is FERNET_KEYS (list).
    key_value = getattr(settings, "FERNET_KEY", None) or " ".join(
        getattr(settings, "FERNET_KEYS", []) or []
    )
    assert LEGACY not in key_value, (
        "Test session must use Fernet.generate_key() — legacy hardcoded value found"
    )
