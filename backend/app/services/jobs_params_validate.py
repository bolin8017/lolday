"""Phase 11e: validate user-supplied job params against the manifest's params_schema.

Replaces the hand-rolled `jobs_params_guard` from Phase 11c. The schema is
auto-derived by maldet from the detector's Pydantic config class at build time
and stored in ``DetectorVersion.manifest.stages.{stage}.params_schema``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import jsonschema


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
