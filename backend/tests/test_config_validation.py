"""Regression guards for Phase 10 SSO production-config validation.

The Settings model_validator fails boot if:
- ENVIRONMENT=production + AUTH_DEV_MODE=true
- ENVIRONMENT=production + empty CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_APP_AUD

These tests ensure a future refactor that relaxes the validator is caught
immediately rather than shipping silent-401 or auth-bypass to prod.
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
