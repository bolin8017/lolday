"""Unit tests for `app.services.http_headers.build_content_disposition`.

`build_content_disposition` is the shared helper that every file-download
route uses to compose the `Content-Disposition` header for an attachment
response. RFC 6266 prescribes the dual-form output
(`filename="<ascii>"; filename*=UTF-8''<percent-encoded>`); the helper
also scrubs quotes from the ASCII fallback so a user-controlled filename
cannot inject `\\r\\n` or `"` into the header line.

Properties pinned here:

1. **Dual-form output** — both `filename="..."` and `filename*=UTF-8''...`
   tokens are present so every modern browser picks the right one.
2. **ASCII fallback degrades non-ASCII to `?`** — Python's
   `str.encode("ascii", errors="replace")` invariant.
3. **Quotes in the ASCII fallback are scrubbed to `_`** — defends against
   a payload like `evil";X-Attack=1.txt` from terminating the
   `filename="..."` token early.
4. **The `filename*` form percent-encodes EVERYTHING outside `safe=""`** —
   no characters are passed through unencoded.
"""

from __future__ import annotations

from app.services.http_headers import build_content_disposition


def test_plain_ascii_filename() -> None:
    out = build_content_disposition("report.pdf")
    assert out == "attachment; filename=\"report.pdf\"; filename*=UTF-8''report.pdf"


def test_dual_form_always_present() -> None:
    """Every output carries BOTH the ASCII fallback and the RFC 5987
    `filename*` form. Older browsers (and curl by default) read the
    fallback; modern browsers prefer `filename*`.
    """
    out = build_content_disposition("a.txt")
    assert 'filename="' in out
    assert "filename*=UTF-8''" in out


def test_non_ascii_degrades_in_fallback() -> None:
    """Non-ASCII chars become `?` in the ASCII fallback (Python's
    `errors='replace'`); the `filename*` form preserves them as
    percent-encoded UTF-8.
    """
    out = build_content_disposition("資料.csv")
    # ASCII fallback: each Chinese char becomes `?`.
    assert 'filename="??.csv"' in out
    # filename* form: percent-encoded UTF-8 of "資料.csv".
    assert "filename*=UTF-8''%E8%B3%87%E6%96%99.csv" in out


def test_quote_in_ascii_fallback_is_scrubbed() -> None:
    """A double-quote in the input would terminate `filename="..."` early
    and inject the rest as new header tokens — classic header-injection
    primitive. The helper replaces `"` with `_` in the ASCII fallback.
    The `filename*` form percent-encodes `"` to `%22`.
    """
    out = build_content_disposition('evil".txt')
    # The fallback must have `_` not the literal `"`.
    assert 'filename="evil_.txt"' in out
    assert 'evil".txt' not in out.split("filename*")[0]
    # The filename* form percent-encodes `"` to `%22`.
    assert "filename*=UTF-8''evil%22.txt" in out


def test_special_chars_percent_encoded_in_filename_star() -> None:
    """`quote(safe="")` encodes EVERY non-alphanumeric, including `.` is
    actually safe in RFC 3986 unreserved set but space, /, +, etc. all
    get encoded.
    """
    out = build_content_disposition("a b/c+d.txt")
    assert "filename*=UTF-8''a%20b%2Fc%2Bd.txt" in out


def test_path_traversal_does_not_break_header() -> None:
    """A user-supplied filename like `../../etc/passwd` is treated as
    plain text (the helper doesn't enforce path semantics — that's the
    caller's job — but it does prevent header injection).
    """
    out = build_content_disposition("../etc/passwd")
    assert 'filename="../etc/passwd"' in out
    # The slash is percent-encoded in the filename* form (per `safe=""`).
    assert "filename*=UTF-8''..%2Fetc%2Fpasswd" in out


def test_empty_filename() -> None:
    """Empty input is unusual but should produce well-formed header text
    rather than crash. Both forms render as empty filename strings.
    """
    out = build_content_disposition("")
    assert out == "attachment; filename=\"\"; filename*=UTF-8''"
