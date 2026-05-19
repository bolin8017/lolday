"""Unit tests for `app.schemas.dataset.DatasetConfigCreate._validate_name`.

The validator runs `@field_validator("name")` on `DatasetConfigCreate`,
trimming whitespace and rejecting names that are empty after the trim.
Layering note (verified below):

- Pydantic's structural checks fire FIRST:
  - `min_length=1` rejects `""`.
  - `pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.\\-]{0,99}$"` requires the first
    character to be alphanumeric and the body to draw from
    `[A-Za-z0-9 _.\\-]`. This pre-rejects:
      - whitespace-only strings (no alphanumeric start)
      - leading whitespace (no alphanumeric start)
      - tab/newline anywhere (not in the body character class)
- Only AFTER the structural checks pass does the validator run. Its
  meaningful effect on the surviving input set is to **strip trailing
  literal spaces** (the only whitespace the pattern admits).

So the validator's `if not v: raise` branch is structurally unreachable
in production — pinned as defence-in-depth, the test confirms the
upstream layers do the work.
"""

from __future__ import annotations

import pytest
from app.schemas.dataset import DatasetConfigCreate
from pydantic import ValidationError


def _make(name: str) -> DatasetConfigCreate:
    """Construct the schema with only `name` varying — the other required
    fields are filled with placeholder valid values.
    """
    return DatasetConfigCreate(
        name=name,
        csv_content="sha256,label\n" + "0" * 64 + ",0\n",
    )


def test_plain_name_passes() -> None:
    assert _make("malware-set-2025q1").name == "malware-set-2025q1"


def test_trailing_space_stripped() -> None:
    """`"alice "` passes the pattern (space is in the body char class),
    then the validator strips → `"alice"`. This is the only path where
    the validator's `strip()` does observable work.
    """
    assert _make("alice ").name == "alice"
    assert _make("alice    ").name == "alice"


def test_internal_whitespace_preserved() -> None:
    """`str.strip()` only touches leading + trailing whitespace, so
    `alice 2025` survives intact (the pattern allows internal spaces).
    """
    assert _make("alice 2025").name == "alice 2025"


def test_min_length_zero_blocked_by_pydantic() -> None:
    """Empty string `""` is rejected by Pydantic's `min_length=1` BEFORE
    the validator runs.
    """
    with pytest.raises(ValidationError) as exc:
        _make("")
    # Pydantic's own min_length error message, not the validator's.
    assert "String should have at least 1 character" in str(exc.value)


def test_whitespace_only_blocked_by_pattern() -> None:
    """A pure-whitespace name is rejected by the pattern (no alphanumeric
    start), before the validator's trim-and-reject branch runs.
    """
    with pytest.raises(ValidationError) as exc:
        _make("   ")
    assert "String should match pattern" in str(exc.value)


def test_leading_whitespace_blocked_by_pattern() -> None:
    """The pattern's `^[A-Za-z0-9]` anchor blocks leading whitespace
    before the validator can strip it (intentional — a name should not
    start with whitespace in the first place).
    """
    with pytest.raises(ValidationError) as exc:
        _make("  alice")
    assert "String should match pattern" in str(exc.value)


def test_tab_or_newline_blocked_by_pattern() -> None:
    """The body character class `[A-Za-z0-9 _.\\-]` admits literal space
    but not tab / newline. A name like `"alice\\t"` reaches the pattern
    check and fails.
    """
    with pytest.raises(ValidationError) as exc:
        _make("alice\t")
    assert "String should match pattern" in str(exc.value)
