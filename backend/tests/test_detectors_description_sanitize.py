"""Detector description must be stripped of <script>, <iframe>, and Markdown link syntax."""

import pytest


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("plain text", "plain text"),
        ("**markdown bold ok**", "**markdown bold ok**"),
        ("<script>alert(1)</script>safe", "safe"),
        ("<SCRIPT>alert(1)</SCRIPT>safe", "safe"),  # case-insensitive
        ("a<script>b</script>c", "ac"),
        ("a<iframe src='x'></iframe>z", "az"),
        ("see [docs](https://example.com)", "see "),
        ("see [docs](javascript:alert(1))", "see "),
        (
            "nested [[a](b)] case",
            "nested [] case",
        ),  # inner [a](b) stripped, outer brackets remain
        ("no link here (a)", "no link here (a)"),  # plain parens preserved
        ("", ""),
        (None, None),
    ],
)
def test_sanitize_detector_description(raw, expected):
    from app.routers.detectors import sanitize_detector_description

    assert sanitize_detector_description(raw) == expected
