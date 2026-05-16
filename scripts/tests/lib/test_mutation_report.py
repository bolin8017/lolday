"""Phase 4 D4.3 — unit tests for scripts/lib/mutation_report.py."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from scripts.lib import mutation_report


def test_render_includes_header_and_table() -> None:
    md = mutation_report.render({}, today=datetime.date(2026, 5, 16))
    assert "# Mutation testing report — 2026-05-16" in md
    assert "| Module |" in md


def test_render_flags_module_below_60_percent() -> None:
    results = {
        "app/routers/jobs.py": {
            "killed": 5,
            "survived": 10,  # 5/15 = 33% < 60%
        }
    }
    md = mutation_report.render(results, today=datetime.date(2026, 5, 16))
    assert "BELOW 60%" in md
    assert "Action items" in md
    assert "33%" in md


def test_render_marks_below_target_but_above_gate() -> None:
    # 7/10 = 70% — above 60%, below 80%.
    results = {
        "app/services/build.py": {
            "killed": 7,
            "survived": 3,
        }
    }
    md = mutation_report.render(results, today=datetime.date(2026, 5, 16))
    assert "below 80%" in md
    assert "BELOW 60%" not in md


def test_render_reports_clean_when_all_pass_gate() -> None:
    results = {
        "app/services/build.py": {
            "killed": 9,
            "survived": 1,
        }
    }
    md = mutation_report.render(results, today=datetime.date(2026, 5, 16))
    assert "meet the Phase 4 exit gate" in md
    assert "Action items" not in md


def test_render_handles_module_with_no_mutants() -> None:
    md = mutation_report.render(
        {"app/models/job.py": {"killed": 0, "survived": 0, "suspicious": 0}},
        today=datetime.date(2026, 5, 16),
    )
    assert "no mutants" in md


def test_main_writes_markdown_file(tmp_path: Path) -> None:
    payload = {"app/models/job.py": {"killed": 3, "survived": 0}}
    in_file = tmp_path / "results.json"
    in_file.write_text(json.dumps(payload))
    out_file = tmp_path / "out.md"
    rc = mutation_report.main(["--input", str(in_file), "--output", str(out_file)])
    assert rc == 0
    text = out_file.read_text()
    assert "100%" in text
    assert "app/models/job.py" in text


def test_main_rejects_neither_input_nor_mutants_dir(tmp_path: Path) -> None:
    """Mutually exclusive required: must provide one of --input / --mutants-dir."""
    import pytest

    with pytest.raises(SystemExit):
        mutation_report.main(["--output", str(tmp_path / "x.md")])
