"""manifest_store: decode base64 OCI label → DetectorManifest."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from app.services.manifest_store import (
    ManifestDecodeError,
    decode_manifest_label,
)

FIX = Path(__file__).parent.parent.parent / "fixtures" / "valid_maldet_manifest.json"


def _b64(j: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(j).encode("utf-8")).decode("ascii")


def test_decode_valid_label() -> None:
    raw = json.loads(FIX.read_text())
    label = _b64(raw)
    manifest = decode_manifest_label(label)
    assert manifest.detector.name == "elfrfdet"
    assert manifest.resources.supports == ["cpu", "gpu1", "gpu2"]


def test_decode_malformed_base64_raises() -> None:
    with pytest.raises(ManifestDecodeError, match="base64"):
        decode_manifest_label("@@@ not base64 @@@")


def test_decode_invalid_json_raises() -> None:
    bad = base64.b64encode(b"not json").decode("ascii")
    with pytest.raises(ManifestDecodeError, match="json"):
        decode_manifest_label(bad)


def test_decode_pydantic_failure_raises() -> None:
    bad_shape = _b64({"detector": {"name": ""}})
    with pytest.raises(ManifestDecodeError, match="manifest"):
        decode_manifest_label(bad_shape)
