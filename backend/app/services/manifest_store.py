"""Decode and persist the OCI-label-embedded maldet DetectorManifest."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from maldet.manifest import DetectorManifest


class ManifestDecodeError(ValueError):
    """Raised when an ``io.maldet.manifest`` label cannot be decoded."""


def decode_manifest_label(label_value: str) -> DetectorManifest:
    """Return the DetectorManifest encoded in a base64 JSON OCI label.

    Raises :class:`ManifestDecodeError` on base64 / JSON / schema failure.
    """
    try:
        raw = base64.b64decode(label_value, validate=True)
    except binascii.Error as exc:
        raise ManifestDecodeError(f"invalid base64: {exc}") from exc
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestDecodeError(f"invalid json: {exc}") from exc
    try:
        return DetectorManifest.model_validate(data)
    except Exception as exc:
        raise ManifestDecodeError(f"manifest schema validation failed: {exc}") from exc
