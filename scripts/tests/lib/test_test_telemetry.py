"""Phase 4 D4.4 — unit tests for scripts/lib/test_telemetry.py."""

from __future__ import annotations

import datetime
import textwrap
from pathlib import Path

import pytest

from scripts.lib import test_telemetry


def _write_junit(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).strip(), encoding="utf-8")


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    art = tmp_path / "artifacts"
    art.mkdir()
    _write_junit(
        art / "junit-1.xml",
        """
        <testsuites>
          <testsuite name="suite1">
            <testcase classname="m.x" name="fast_a" time="0.01"/>
            <testcase classname="m.x" name="fast_b" time="0.05"/>
            <testcase classname="m.x" name="slow_c" time="42.5"/>
          </testsuite>
        </testsuites>
        """,
    )
    _write_junit(
        art / "junit-2.xml",
        """
        <testsuites>
          <testsuite name="suite1">
            <testcase classname="m.x" name="fast_a" time="0.02">
              <failure message="boom">Trace</failure>
            </testcase>
            <testcase classname="m.x" name="fast_b" time="0.04"/>
            <testcase classname="m.x" name="slow_c" time="25.0"/>
          </testsuite>
        </testsuites>
        """,
    )
    return art


def test_parse_junit_dir_aggregates_stats(fixture_dir: Path) -> None:
    stats = test_telemetry.parse_junit_dir(fixture_dir)
    assert set(stats.keys()) == {"m.x::fast_a", "m.x::fast_b", "m.x::slow_c"}
    assert stats["m.x::fast_a"]["runs"] == 2
    assert stats["m.x::fast_a"]["fails"] == 1
    assert stats["m.x::fast_a"]["durations"] == [0.01, 0.02]


def test_render_dashboard_flags_flaky_and_slow(fixture_dir: Path) -> None:
    stats = test_telemetry.parse_junit_dir(fixture_dir)
    md = test_telemetry.render_dashboard(stats, today=datetime.date(2026, 5, 16))
    assert "`m.x::slow_c`" in md
    assert "50.0%" in md
    # P99 with only two samples interpolates near the upper bound; both rounding
    # forms ("42.4s" / "42.5s") are acceptable — assert the slow_c P99 row exists.
    assert "P99 = 42" in md or "P99 = 42.5s" in md


def test_render_discord_summary_is_six_lines(fixture_dir: Path) -> None:
    stats = test_telemetry.parse_junit_dir(fixture_dir)
    summary = test_telemetry.render_discord_summary(stats)
    body_lines = summary.split("\n")
    # Header + 5 content lines = 6 lines.
    assert len(body_lines) == 6
    assert "weekly summary" in body_lines[0]


def test_parse_junit_dir_skips_malformed_xml(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "junit-broken.xml").write_text("<not-xml>")
    (art / "junit-ok.xml").write_text(
        '<testsuites><testsuite name="s"><testcase classname="x" name="y" time="1"/></testsuite></testsuites>'
    )
    stats = test_telemetry.parse_junit_dir(art)
    assert "x::y" in stats


def test_render_dashboard_empty_clean(tmp_path: Path) -> None:
    md = test_telemetry.render_dashboard({}, today=datetime.date(2026, 5, 16))
    assert "None this week" in md
    assert "Total tests tracked: **0**" in md


def test_main_writes_both_outputs(fixture_dir: Path, tmp_path: Path) -> None:
    dash = tmp_path / "dash.md"
    summary = tmp_path / "summary.txt"
    rc = test_telemetry.main(
        [str(fixture_dir), "--dashboard-out", str(dash), "--summary-out", str(summary)]
    )
    assert rc == 0
    assert dash.exists()
    assert summary.exists()
    assert "weekly summary" in summary.read_text()


# ---------------------------------------------------------------------------
# _percentile edge cases + main without --summary-out. These three small
# branches are the residual gap after the existing tier-3 coverage.
# ---------------------------------------------------------------------------


def test_percentile_empty_list_returns_zero() -> None:
    """An empty sample list must surface as P50/95/99 = 0.0 rather than
    crashing on `sorted([])[0]`. parse_junit_dir already filters durations
    in the per-test path, but render_dashboard re-enters `_percentile` on
    fresh data the next week — defend the helper itself."""
    assert test_telemetry._percentile([], 50.0) == 0.0
    assert test_telemetry._percentile([], 95.0) == 0.0
    assert test_telemetry._percentile([], 99.0) == 0.0


def test_percentile_single_sample_returns_that_sample() -> None:
    """A one-sample series has the same value at every percentile.
    Tests new to the repo hit this case on their first execution."""
    assert test_telemetry._percentile([7.5], 50.0) == 7.5
    assert test_telemetry._percentile([7.5], 99.0) == 7.5


def test_percentile_returns_exact_value_when_index_is_integer() -> None:
    """When ``(len(s) - 1) * pct / 100`` lands on an integer, no
    interpolation is needed (`lo == hi`). A two-sample series at P0 / P100
    is the canonical trigger."""
    # P0 over [1.0, 2.0]: k = 1*0/100 = 0 → s[0] = 1.0
    assert test_telemetry._percentile([2.0, 1.0], 0.0) == 1.0
    # P100 over [1.0, 2.0]: k = 1*100/100 = 1 → s[1] = 2.0
    assert test_telemetry._percentile([1.0, 2.0], 100.0) == 2.0
    # A three-sample, P50 series also lands cleanly: k = 2 * 50 / 100 = 1
    # → s[1] = 2.0 (the median for an odd-count series).
    assert test_telemetry._percentile([1.0, 2.0, 3.0], 50.0) == 2.0


def test_main_without_summary_out_skips_discord_summary(
    fixture_dir: Path, tmp_path: Path
) -> None:
    """``--summary-out`` is optional; omitting it must NOT crash and must
    NOT write a discord summary file. The dashboard alone is enough for
    the weekly markdown drop into docs/test-telemetry/."""
    dash = tmp_path / "dash.md"
    summary_candidate = tmp_path / "summary.txt"
    rc = test_telemetry.main([str(fixture_dir), "--dashboard-out", str(dash)])
    assert rc == 0
    assert dash.exists()
    # The summary-out default is None — no companion file appears.
    assert not summary_candidate.exists()
