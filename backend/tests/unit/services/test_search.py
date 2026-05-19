"""Unit tests for `app.services.search.escape_like_pattern`.

`escape_like_pattern` is the single helper that wraps user-supplied search
strings before they are injected into a SQLAlchemy `ilike("%<x>%",
escape="\\")` predicate. The routers under `datasets.py` + `detectors.py`
funnel every search-string param through this helper; if it ever stops
escaping `%`, `_`, or `\\`, an arbitrary substring becomes a wildcard
matcher and the search endpoint becomes a covert "list everything" surface.

Two security properties this helper enforces:

1. **`%` and `_` are LIKE wildcards** — escape both so they match a literal
   character, not "any sequence" / "any single character".
2. **`\\` is the escape character** — escape it FIRST so a user-typed
   `\\%` doesn't smuggle in an unescaped wildcard via the escape's escape
   character.

Property #2 implies an ordering constraint: escape `\\` before `%`/`_`.
The tests below pin that ordering via an attack-style payload.
"""

from __future__ import annotations

from app.services.search import escape_like_pattern


def test_pass_through_plain_text() -> None:
    """Plain alphanumerics are untouched (escape is no-op for safe input)."""
    assert escape_like_pattern("alice") == "alice"
    assert escape_like_pattern("v1.0.0") == "v1.0.0"


def test_empty_string_is_pass_through() -> None:
    assert escape_like_pattern("") == ""


def test_percent_wildcard_is_escaped() -> None:
    """`%` matches "any sequence" in LIKE — must escape to literal."""
    assert escape_like_pattern("%") == "\\%"
    assert escape_like_pattern("100%match") == "100\\%match"


def test_underscore_wildcard_is_escaped() -> None:
    """`_` matches "any single character" in LIKE — must escape to literal."""
    assert escape_like_pattern("_") == "\\_"
    assert escape_like_pattern("foo_bar") == "foo\\_bar"


def test_backslash_is_escaped_first() -> None:
    """Bare `\\` must double to `\\` BEFORE `%`/`_` are escaped.

    Attack payload: `\\%`. If the order were `% → \\%` followed by `\\ → \\`,
    the result would be `\\\\%` (four backslashes + literal %, parsed as
    escape-of-escape + raw %) and the `%` would still be a wildcard.
    Correct order yields `\\\\\\%` (two backslashes + escaped %), which
    SQLAlchemy parses as one literal `\\` then a literal `%`.
    """
    assert escape_like_pattern("\\") == "\\\\"
    # Ordering pin: `\%` must NOT collapse the % back into a wildcard.
    # Expected output: `\\` for the backslash, then `\%` for the literal %.
    assert escape_like_pattern("\\%") == "\\\\\\%"


def test_underscore_after_backslash_is_escaped() -> None:
    """Mirror of the `\\%` ordering test for the underscore wildcard."""
    assert escape_like_pattern("\\_") == "\\\\\\_"


def test_combined_payload_escapes_every_metacharacter() -> None:
    """A pathological combined payload exercises every replacement branch."""
    # Input: `\` `%` `_` `\` (with literal text between to prove order).
    src = "a\\b%c_d\\"
    out = escape_like_pattern(src)
    # Each `\` → `\\`; each `%` → `\%`; each `_` → `\_`. After substitution
    # the literal chars `a`, `b`, `c`, `d` are untouched.
    assert out == "a\\\\b\\%c\\_d\\\\"


def test_unicode_pass_through() -> None:
    """Non-ASCII characters are untouched — escape is wildcard-only."""
    assert escape_like_pattern("中文_search") == "中文\\_search"
    assert escape_like_pattern("naïve") == "naïve"
