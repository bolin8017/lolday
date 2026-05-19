"""Unit tests for `app.schemas.user._validate_discord_user_id`.

The validator runs in `mode="before"` on `UserSelfUpdate.discord_user_id`,
so it sees the raw inbound JSON value before Pydantic coerces it. Five
branches need to stay covered:

1. `None` → `None` (clear-the-field)
2. empty string `""` → `None` (HTML form sends empty rather than null)
3. **non-str** (dict / int / list) → `ValueError` (the schemathesis v4
   hardening — without this branch, `re.Pattern.match(<dict>)` raises
   `TypeError` and escapes as a 500 instead of the expected 422)
4. wrong format (`abc`, `1234`, too long) → `ValueError` with the
   "15-20 digits" message
5. valid Discord snowflake (15-20 decimal digits) → echoed back

Integration tests at `tests/integration/routers/test_user_discord_id.py`
cover branches 1, 2, 4, 5 via HTTP. Branch 3 (non-str) lacked direct
coverage; schemathesis v4 surfaced it via `{"discord_user_id": {}}` and
the regression was the wrong-status-code path described above.
"""

from __future__ import annotations

import pytest
from app.schemas.user import _validate_discord_user_id


def test_none_passes_through() -> None:
    assert _validate_discord_user_id(None) is None


def test_empty_string_coerced_to_none() -> None:
    """HTML form fields send `""` rather than `null`; the user clearing
    their Discord ID via the UI must reach the DB as `NULL`, not a
    literal empty string.
    """
    assert _validate_discord_user_id("") is None


@pytest.mark.parametrize(
    "bad",
    [
        {},  # dict — what schemathesis v4 sent
        [],  # list
        123456789012345678,  # int (Discord IDs ARE 64-bit ints, but the
        # schema declares str; mode='before' must reject)
        True,  # bool
        12.3,  # float
    ],
)
def test_non_str_raises_valueerror(bad: object) -> None:
    """Without the `isinstance(v, str)` guard, `re.Pattern.match` raises
    `TypeError` which Pydantic does NOT wrap, surfacing as 500 instead
    of 422. The branch must raise `ValueError` so Pydantic produces the
    documented 422 response.
    """
    with pytest.raises(ValueError, match="discord_user_id must be a string"):
        _validate_discord_user_id(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "abc",  # non-digit
        "1234",  # too short (< 15)
        "123456789012345678901",  # too long (21 chars, > 20)
        "12345678901234567a",  # mixed digit + letter
        " 123456789012345",  # leading whitespace (re uses anchored ^$)
    ],
)
def test_wrong_format_raises_valueerror(bad: str) -> None:
    with pytest.raises(ValueError, match="15-20 digits"):
        _validate_discord_user_id(bad)


@pytest.mark.parametrize(
    "good",
    [
        "123456789012345",  # 15 digits (lower bound)
        "12345678901234567890",  # 20 digits (upper bound)
        "987654321098765432",  # 18 digits (typical Discord snowflake)
    ],
)
def test_valid_snowflake_passes_through(good: str) -> None:
    assert _validate_discord_user_id(good) == good
