"""Aggregate the last 7 days of JUnit XML test reports; emit a flaky-test
issue for any test with failure rate > 1%.

Invoked from .github/workflows/flaky-tracker.yml on a weekly cron schedule.
Per .claude/rules/testing.md quarantine workflow: any test with
failure rate > 1% over 7 days triggers an auto-issue with the 'flaky'
label, 14d fix SLO, 21d delete SLO.

Usage:
    python scripts/lib/flaky_aggregate.py <artifact_dir>

Environment:
    GH_REPO: owner/repo (e.g., bolin8017/lolday) — defaults to env or inferred
    GH_TOKEN: GitHub token with issues:write
"""

from __future__ import annotations

import collections
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_GH_REPO = os.environ.get("GH_REPO", "bolin8017/lolday")
THRESHOLD = float(os.environ.get("FLAKY_THRESHOLD", "0.01"))
MIN_RUNS = int(os.environ.get("FLAKY_MIN_RUNS", "10"))  # don't flag based on <10 runs


def parse_runs(artifact_dir: Path) -> dict[str, dict[str, int]]:
    """Walk artifact dir, parse every junit*.xml file, return per-test
    {total, fail} stats."""
    stats: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"total": 0, "fail": 0}
    )
    for xml in artifact_dir.rglob("junit*.xml"):
        try:
            tree = ET.parse(xml)  # local artifact, not network input
        except ET.ParseError as e:
            print(f"[warn] skipping malformed XML {xml}: {e}", file=sys.stderr)
            continue
        for case in tree.iterfind(".//testcase"):
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            stats[name]["total"] += 1
            if case.find("failure") is not None or case.find("error") is not None:
                stats[name]["fail"] += 1
    return stats


def open_issue(repo: str, name: str, fail: int, total: int, rate: float) -> None:
    title = f"Flaky test: {name} ({rate:.1%} over {total} runs)"
    body = (
        f"`{name}` failed {fail}/{total} times in the last 7 days "
        f"(failure rate {rate:.1%}).\n\n"
        f"Per `.claude/rules/testing.md`: 14d fix SLO, 21d delete SLO.\n\n"
        f"Mark the test with `@pytest.mark.flaky_tracked(issue=<this issue URL>)` "
        f"to acknowledge."
    )
    subprocess.run(  # fixed args, no shell=True
        [
            "gh",
            "issue",
            "create",
            "-R",
            repo,
            "-t",
            title,
            "-l",
            "flaky",
            "-b",
            body,
        ],
        check=True,
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: flaky_aggregate.py <artifact_dir>", file=sys.stderr)
        return 2

    artifact_dir = Path(argv[1])
    if not artifact_dir.exists():
        print(f"[error] artifact dir {artifact_dir} does not exist", file=sys.stderr)
        return 1

    stats = parse_runs(artifact_dir)
    flaky = [
        (name, s["fail"], s["total"], s["fail"] / s["total"])
        for name, s in stats.items()
        if s["total"] >= MIN_RUNS and s["fail"] / s["total"] > THRESHOLD
    ]
    flaky.sort(key=lambda x: -x[3])

    print(
        f"Analyzed {len(stats)} unique tests; "
        f"{len(flaky)} flagged as flaky (rate > {THRESHOLD:.0%})"
    )

    for name, fail, total, rate in flaky:
        print(f"  {name}: {fail}/{total} ({rate:.1%})")
        if os.environ.get("FLAKY_DRY_RUN") != "1":
            open_issue(DEFAULT_GH_REPO, name, fail, total, rate)
        else:
            print("  (dry run; no issue created)")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
