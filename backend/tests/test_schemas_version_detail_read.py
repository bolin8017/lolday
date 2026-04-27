"""VersionDetailRead exposes the full manifest for typed-form rendering."""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid

from app.models.detector import DetectorVersionStatus
from app.schemas.detector import VersionDetailRead


def test_version_detail_read_has_manifest_field() -> None:
    fields = VersionDetailRead.model_fields
    assert "manifest" in fields


def test_version_detail_read_serializes_manifest() -> None:
    payload = {
        "id": _uuid.uuid4(),
        "git_tag": "v3.0.0",
        "git_sha": "0" * 40,
        "harbor_image": "harbor.example/x:v3.0.0",
        "image_digest": "sha256:abc",
        "built_at": _dt.datetime.now(_dt.timezone.utc),
        "status": DetectorVersionStatus.ACTIVE,
        "manifest": {
            "detector": {"name": "x", "version": "3.0.0"},
            "stages": {
                "train": {
                    "config_class": "x.configs:TrainConfig",
                    "params_schema": {"type": "object"},
                }
            },
        },
    }
    obj = VersionDetailRead.model_validate(payload)
    assert obj.manifest["stages"]["train"]["config_class"] == "x.configs:TrainConfig"
    assert obj.manifest["stages"]["train"]["params_schema"] == {"type": "object"}
