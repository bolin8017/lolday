"""HTTP header helpers shared across routers."""

from urllib.parse import quote


def build_content_disposition(filename: str) -> str:
    """RFC 6266 dual-form ``Content-Disposition``.

    Output: ``attachment; filename="<ascii>"; filename*=UTF-8''<percent-encoded>``.

    Non-ASCII chars in the ASCII fallback become ``?`` and quotes are scrubbed
    to ``_`` to defend against header-injection. The ``filename*`` form is
    used by every modern browser.
    """
    ascii_fallback = (
        filename.encode("ascii", errors="replace").decode("ascii").replace('"', "_")
    )
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"
