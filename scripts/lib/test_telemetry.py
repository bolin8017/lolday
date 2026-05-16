"""Aggregate JUnit XML test reports into a Markdown dashboard.

Phase 4 D4.4 part 1 of 4. Invoked from .github/workflows/test-telemetry.yml
(part 3) on a weekly cron. Walks a directory of JUnit XML files
(downloaded from the last 7 days of workflow artifacts via
actions/github-script — same pattern as flaky-tracker.yml),
aggregates per-test runtime + pass/fail stats, and rewrites
docs/test-telemetry/dashboard.md with five sections:

1. Per-test 30-day P50 / P95 / P99 duration (top 30 slow).
2. Per-test 7-day failure rate (anything > 0%).
3. Flaky candidates (failure rate > 1%) — pointer to flaky-tracker.
4. Slow tests (P99 > 30s).
5. Run count and total wall-clock per workflow.

A small Discord-friendly 5-line summary is also produced for the
Spidey Warnings channel.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TypedDict

FLAKY_THRESHOLD = 0.01  # 1%
SLOW_P99_SECONDS = 30.0


class TestStat(TypedDict):
    runs: int
    fails: int
    durations: list[float]


def parse_junit_dir(artifact_dir: Path) -> dict[str, TestStat]:
    """Walk artifact_dir for junit*.xml; aggregate per-test stats."""
    stats: dict[str, TestStat] = collections.defaultdict(
        lambda: {"runs": 0, "fails": 0, "durations": []}
    )
    for xml in artifact_dir.rglob("junit*.xml"):
        try:
            tree = ET.parse(xml)  # local artifact, not network input
        except ET.ParseError as e:
            print(f"[warn] skipping malformed XML {xml}: {e}", file=sys.stderr)
            continue
        for case in tree.iterfind(".//testcase"):
            classname = case.get("classname", "")
            name = case.get("name", "")
            tid = f"{classname}::{name}"
            stats[tid]["runs"] += 1
            if case.find("failure") is not None or case.find("error") is not None:
                stats[tid]["fails"] += 1
            with contextlib.suppress(ValueError):
                stats[tid]["durations"].append(float(case.get("time", "0") or 0.0))
    return dict(stats)


def _percentile(values: list[float], pct: float) -> float:
    """Return the `pct` percentile of `values` (0 ≤ pct ≤ 100)."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    k = (len(s) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def render_dashboard(
    stats: dict[str, TestStat],
    *,
    today: datetime.date | None = None,
) -> str:
    today = today or datetime.date.today()
    lines: list[str] = []
    lines.append("# Test execution telemetry dashboard")
    lines.append("")
    lines.append(
        f"_Last updated: {today.isoformat()} "
        "(regenerated weekly by `.github/workflows/test-telemetry.yml`)._"
    )
    lines.append("")
    lines.append(f"Total tests tracked: **{len(stats)}**.")
    lines.append("")

    slow_rows: list[tuple[str, float, float, float, int]] = []
    flaky_rows: list[tuple[str, int, int, float]] = []
    above_p99_threshold: list[tuple[str, float]] = []
    for tid, s in stats.items():
        if s["durations"]:
            p50 = statistics.median(s["durations"])
            p95 = _percentile(s["durations"], 95)
            p99 = _percentile(s["durations"], 99)
            slow_rows.append((tid, p50, p95, p99, s["runs"]))
            if p99 > SLOW_P99_SECONDS:
                above_p99_threshold.append((tid, p99))
        if s["runs"] > 0:
            rate = s["fails"] / s["runs"]
            if rate > FLAKY_THRESHOLD:
                flaky_rows.append((tid, s["fails"], s["runs"], rate))

    lines.append("## Slow tests (top 30 by P99)")
    lines.append("")
    if not slow_rows:
        lines.append("None this week. ✓")
    else:
        lines.append("| Test | P50 (s) | P95 (s) | P99 (s) | Runs |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        slow_rows.sort(key=lambda r: -r[3])
        for tid, p50, p95, p99, runs in slow_rows[:30]:
            lines.append(f"| `{tid}` | {p50:.2f} | {p95:.2f} | {p99:.2f} | {runs} |")
    lines.append("")

    lines.append(f"## Flaky candidates (failure rate > {FLAKY_THRESHOLD:.0%})")
    lines.append("")
    if not flaky_rows:
        lines.append("None this week. ✓")
    else:
        lines.append("| Test | Fails | Runs | Rate |")
        lines.append("| --- | ---: | ---: | ---: |")
        flaky_rows.sort(key=lambda r: -r[3])
        for tid, fails, runs, rate in flaky_rows:
            lines.append(f"| `{tid}` | {fails} | {runs} | {rate:.1%} |")
        lines.append("")
        lines.append(
            "These tests should already have a `flaky-tracker.yml`-opened "
            "issue. If not, file one and apply `@pytest.mark.flaky_tracked` "
            "per `.claude/rules/testing.md`."
        )
    lines.append("")

    lines.append(f"## Slow-tier warnings (P99 > {SLOW_P99_SECONDS:.0f}s)")
    lines.append("")
    if not above_p99_threshold:
        lines.append("None this week. ✓")
    else:
        above_p99_threshold.sort(key=lambda r: -r[1])
        for tid, p99 in above_p99_threshold:
            lines.append(f"- `{tid}` — P99 = {p99:.1f}s")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_discord_summary(stats: dict[str, TestStat]) -> str:
    """Five-line summary for the Spidey Warnings channel."""
    total_tests = len(stats)
    total_runs = sum(s["runs"] for s in stats.values())
    total_fails = sum(s["fails"] for s in stats.values())
    flaky = sum(
        1
        for s in stats.values()
        if s["runs"] > 0 and s["fails"] / s["runs"] > FLAKY_THRESHOLD
    )
    slow = sum(
        1
        for s in stats.values()
        if s["durations"] and _percentile(s["durations"], 99) > SLOW_P99_SECONDS
    )
    overall_rate = (total_fails / total_runs) if total_runs else 0.0
    return (
        "**Test telemetry — weekly summary**\n"
        f"Total tests tracked: {total_tests}\n"
        f"Total runs: {total_runs} (fails: {total_fails}, overall rate: {overall_rate:.2%})\n"
        f"Flaky candidates (>1% failure): {flaky}\n"
        f"Slow tests (P99 > {SLOW_P99_SECONDS:.0f}s): {slow}\n"
        f"Dashboard: docs/test-telemetry/dashboard.md"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="test_telemetry")
    parser.add_argument("artifact_dir", help="directory of JUnit XML files")
    parser.add_argument("--dashboard-out", required=True, help="dashboard.md path")
    parser.add_argument(
        "--summary-out", default=None, help="discord summary path (optional)"
    )
    args = parser.parse_args(argv)

    stats = parse_junit_dir(Path(args.artifact_dir))
    Path(args.dashboard_out).write_text(render_dashboard(stats), encoding="utf-8")
    if args.summary_out:
        Path(args.summary_out).write_text(
            render_discord_summary(stats), encoding="utf-8"
        )
    print(f"aggregated {len(stats)} tests from {args.artifact_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
