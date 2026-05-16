"""Render mutmut 3.x results as a Markdown report.

Phase 4 D4.3 part 3 of 4. Invoked by .github/workflows/mutation.yml
(part 4). mutmut 3.x stores per-source-file mutation state in
``mutants/<path>.meta``; this module loads each via mutmut's internal
``SourceFileMutationData`` + ``collect_stat`` (the same path mutmut's
own ``export-cicd-stats`` command uses to aggregate), then renders
a per-module Markdown table to
``docs/test-telemetry/mutation-<YYYY-MM-DD>.md``.

For unit tests + local rendering, ``render()`` accepts a plain
``dict[str, dict[str, int]]`` (per-module counts) so the test suite
does not need a populated ``mutants/`` directory.

Phase 4 exit gate: ≥ 60% killed per module. Eventual target: ≥ 80%.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import TypedDict

PHASE_4_KILL_THRESHOLD = 0.60
PHASE_4_TARGET = 0.80

COUNT_KEYS = ("killed", "survived", "suspicious", "skipped", "no_tests", "timeout")


class ModuleStats(TypedDict, total=False):
    killed: int
    survived: int
    suspicious: int
    skipped: int
    no_tests: int
    timeout: int


def render(
    results: dict[str, ModuleStats], *, today: datetime.date | None = None
) -> str:
    """Format the per-module results dict as a Markdown report string."""
    today = today or datetime.date.today()
    lines: list[str] = []
    lines.append(f"# Mutation testing report — {today.isoformat()}")
    lines.append("")
    lines.append(
        "Phase 4 D4.3 — weekly cron output. Threshold for Phase 4 exit: ≥ 60%; target ≥ 80%."
    )
    lines.append("")
    lines.append(
        "| Module | Killed | Survived | Suspicious | Skipped | No-tests | Timeout | Kill-rate | Flag |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")

    below: list[str] = []
    for module in sorted(results):
        bucket = results[module]
        killed = int(bucket.get("killed", 0))
        survived = int(bucket.get("survived", 0))
        suspicious = int(bucket.get("suspicious", 0))
        skipped = int(bucket.get("skipped", 0))
        no_tests = int(bucket.get("no_tests", 0))
        timeout = int(bucket.get("timeout", 0))
        denom = killed + survived + suspicious
        if denom == 0:
            rate_str = "n/a"
            flag = "no mutants"
        else:
            kill_rate = killed / denom
            rate_str = f"{kill_rate:.0%}"
            flag = ""
            if kill_rate < PHASE_4_KILL_THRESHOLD:
                flag = "BELOW 60%"
                below.append(f"- `{module}` killed {kill_rate:.0%} ({killed}/{denom})")
            elif kill_rate < PHASE_4_TARGET:
                flag = "below 80%"
        lines.append(
            f"| `{module}` | {killed} | {survived} | {suspicious} | {skipped} | {no_tests} | {timeout} | {rate_str} | {flag} |"
        )

    lines.append("")
    if below:
        lines.append("## Action items (kill-rate < 60%)")
        lines.append("")
        lines.extend(below)
        lines.append("")
    else:
        lines.append("All targeted modules meet the Phase 4 exit gate (≥ 60%).")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def collect_from_mutants_dir(mutants_dir: Path) -> dict[str, ModuleStats]:
    """Use mutmut's internal ``SourceFileMutationData`` / ``collect_stat``
    to walk every ``*.meta`` under ``mutants_dir`` and emit per-module
    counts in the shape ``render()`` expects.

    Requires the current working directory to contain the source tree
    mutmut was invoked against (the ``.meta`` files reference source
    paths relative to that root).
    """
    try:
        from mutmut.__main__ import SourceFileMutationData, collect_stat
    except ImportError as e:
        raise RuntimeError(
            "mutmut is not installed; run from the backend uv env"
        ) from e

    out: dict[str, ModuleStats] = {}
    for meta in mutants_dir.rglob("*.meta"):
        source_path = meta.with_suffix("")
        rel = source_path.relative_to(mutants_dir)
        m = SourceFileMutationData(path=rel)
        m.load()
        if not m.exit_code_by_key:
            continue
        s = collect_stat(m)
        out[str(rel)] = {
            "killed": s.killed,
            "survived": s.survived,
            "suspicious": s.suspicious,
            "skipped": s.skipped,
            "no_tests": s.no_tests,
            "timeout": s.timeout,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mutation_report")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--input", help="JSON input file with the per-module dict (or '-' for stdin)"
    )
    src.add_argument(
        "--mutants-dir",
        help="Directory holding mutmut's per-source .meta files (typically backend/mutants)",
    )
    parser.add_argument("--output", required=True, help="Markdown output path")
    args = parser.parse_args(argv)

    if args.input is not None:
        if args.input == "-":
            results = json.load(sys.stdin)
        else:
            with open(args.input, encoding="utf-8") as f:
                results = json.load(f)
    else:
        results = collect_from_mutants_dir(Path(args.mutants_dir))

    md = render(results)
    Path(args.output).write_text(md, encoding="utf-8")
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
