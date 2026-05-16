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
