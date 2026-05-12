"""Shared helpers for search-string handling.

LIKE / ILIKE patterns use ``%`` and ``_`` as wildcards. When a search
string comes from user input we must escape those characters so a user
typing ``%`` matches a literal percent sign, not "everything".
"""


def escape_like_pattern(s: str) -> str:
    """Escape ``\\``, ``%``, and ``_`` so the result is safe inside ``ilike("%<x>%", escape="\\\\")``."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
