"""Utilities over the manifest's per-stage ``params_schema``.

The schema is auto-derived by maldet from the detector's Pydantic config class
at build time and stored in ``DetectorVersion.manifest.stages.{stage}.params_schema``
(JSON Schema Draft 2020-12).

Two responsibilities live here:

* :func:`validate_user_params` — Phase 11e: reject user-submitted params that
  don't satisfy the schema. Replaces the hand-rolled ``jobs_params_guard`` from
  Phase 11c.
* :func:`resolve_detector_defaults` — Phase 13b Q1: extract the per-field
  ``default`` map for the override-indicator UI on ``JobRead``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import jsonschema

from app.models.job import JobType


class UserParamsRejected(ValueError):
    """Raised when user params don't satisfy the stage's JSON Schema."""


def _format_pointer(absolute_path: Iterable[Any]) -> str:
    parts = [str(p) for p in absolute_path]
    return "/" + "/".join(parts) if parts else "/"


def validate_user_params(*, params: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate ``params`` against ``schema`` (JSON Schema Draft 2020-12).

    Raises :class:`UserParamsRejected` with one diagnostic per error
    (`{json_pointer}: {message}`), aggregated across all errors so the user
    sees every problem at once.
    """
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(params), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    detail = "; ".join(
        f"{_format_pointer(e.absolute_path)}: {e.message}" for e in errors
    )
    raise UserParamsRejected(detail)


def resolve_detector_defaults(
    manifest: dict[str, Any] | None, job_type: JobType
) -> dict[str, Any] | None:
    """Extract per-field defaults from the stage's params_schema.

    Returns the ``{<field>: <default>}`` map taken verbatim from
    ``manifest.stages[<job_type>].params_schema.properties`` for each field
    that declares a ``default`` key. Returns ``None`` (not ``{}``) when the
    manifest is missing, the stage block isn't there, or no field declares a
    default — the override-indicator UI uses ``null`` to hide the comparison
    column entirely.

    Reads the raw JSON dict (not the parsed ``DetectorManifest``) so the
    helper isn't tied to a particular maldet minor version: ``params_schema``
    is open-ended (a JSON Schema authored by detector authors), and the
    Pydantic model normalizes the schema while preserving ``default`` values
    verbatim. Using ``"default" in v`` rather than ``v.get("default")``
    preserves the "default is null" vs "no default declared" distinction
    (e.g. sklearn ``max_depth: None`` is a meaningful default).
    """
    if manifest is None:
        return None
    stage = (manifest.get("stages") or {}).get(job_type.value)
    if not stage:
        return None
    props = (stage.get("params_schema") or {}).get("properties") or {}
    defaults = {
        k: v["default"]
        for k, v in props.items()
        if isinstance(v, dict) and "default" in v
    }
    return defaults or None
