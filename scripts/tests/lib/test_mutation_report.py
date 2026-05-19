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


# ---------------------------------------------------------------------------
# collect_from_mutants_dir + main's stdin / --mutants-dir branches.
# Phase 4 D4.3's weekly mutation.yml workflow drives this path: it points
# --mutants-dir at backend/mutants/ and writes the markdown to
# docs/test-telemetry/mutation-<date>.md. Without coverage, a refactor
# of the collect/main wiring would silently produce empty reports.
# ---------------------------------------------------------------------------


def test_main_reads_input_from_stdin(tmp_path: Path, monkeypatch, capsys) -> None:
    """``--input -`` reads JSON from stdin — the workflow uses this when
    upstream tooling pipes export-cicd-stats output into the renderer."""
    from io import StringIO

    payload = {"app/services/build.py": {"killed": 4, "survived": 1}}
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
    out_file = tmp_path / "out.md"
    rc = mutation_report.main(["--input", "-", "--output", str(out_file)])
    assert rc == 0
    text = out_file.read_text()
    assert "app/services/build.py" in text
    # 4/5 = 80% — exactly at the target, not below.
    assert "80%" in text
    assert "wrote" in capsys.readouterr().err


def test_main_with_mutants_dir_calls_collect(tmp_path: Path, monkeypatch) -> None:
    """``--mutants-dir`` triggers the live mutmut-backed path (line 154).
    Mock collect_from_mutants_dir so the test doesn't need a populated
    .meta directory; we're pinning the wiring, not the mutmut internals."""
    mutants = tmp_path / "mutants"
    mutants.mkdir()
    captured: dict[str, Path] = {}

    def fake_collect(d: Path):
        captured["dir"] = d
        return {"app/models/job.py": {"killed": 9, "survived": 1}}

    monkeypatch.setattr(mutation_report, "collect_from_mutants_dir", fake_collect)
    out_file = tmp_path / "out.md"
    rc = mutation_report.main(
        ["--mutants-dir", str(mutants), "--output", str(out_file)]
    )
    assert rc == 0
    assert captured["dir"] == mutants
    assert "app/models/job.py" in out_file.read_text()


def test_collect_from_mutants_dir_empty_dir_returns_empty(tmp_path: Path) -> None:
    """No ``.meta`` files → empty dict, no crash. Defends against the
    ``mutants/`` PVC mounting before the first mutmut run lands."""
    empty = tmp_path / "mutants"
    empty.mkdir()
    assert mutation_report.collect_from_mutants_dir(empty) == {}


def test_collect_from_mutants_dir_raises_when_mutmut_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """If mutmut isn't installed in the env (e.g. the runner missed the
    `uv sync --extra dev` step), the helper must raise a clear RuntimeError
    instead of letting an ImportError propagate."""
    import builtins

    import pytest

    real_import = builtins.__import__

    def deny_mutmut(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: object = (),
        level: int = 0,
    ):
        if name == "mutmut.__main__":
            raise ImportError("simulated mutmut absence")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", deny_mutmut)
    with pytest.raises(RuntimeError, match="mutmut is not installed"):
        mutation_report.collect_from_mutants_dir(tmp_path)


def test_collect_from_mutants_dir_skips_meta_with_no_results(
    tmp_path: Path, monkeypatch
) -> None:
    """A ``.meta`` file with an empty ``exit_code_by_key`` map represents a
    mutmut pre-run state — skip it rather than emit a zero-count row that
    would dilute the per-module kill-rate. Achieved by stubbing
    mutmut.SourceFileMutationData + collect_stat so the test does not need
    a real mutmut serialised file format."""
    mutants = tmp_path / "mutants"
    sub = mutants / "app" / "services"
    sub.mkdir(parents=True)
    # Two .meta files: one with no results (skipped), one with results.
    (sub / "build.py.meta").write_text("not real mutmut format")
    (sub / "harbor.py.meta").write_text("not real mutmut format")

    class FakeSFMD:
        def __init__(self, *, path):
            self.path = path
            self.exit_code_by_key = {} if path.name == "build.py" else {"a": 0}

        def load(self) -> None:
            pass

    class FakeStat:
        def __init__(self):
            self.killed = 4
            self.survived = 1
            self.suspicious = 0
            self.skipped = 0
            self.no_tests = 0
            self.timeout = 0

    # Inject the stubs into the dynamic import path used by
    # collect_from_mutants_dir (``from mutmut.__main__ import ...``).
    import sys as _sys
    import types

    fake_main = types.ModuleType("mutmut.__main__")
    # type: ignore reasons: dynamic ModuleType has no statically known attrs;
    # the stubs satisfy the `from mutmut.__main__ import ...` line at runtime.
    fake_main.SourceFileMutationData = FakeSFMD  # type: ignore[attr-defined]  # dynamic stub on ModuleType
    fake_main.collect_stat = lambda m: FakeStat()  # type: ignore[attr-defined]  # dynamic stub on ModuleType
    monkeypatch.setitem(_sys.modules, "mutmut.__main__", fake_main)

    out = mutation_report.collect_from_mutants_dir(mutants)
    # Only harbor.py.meta produced a row; build.py.meta was skipped.
    assert "app/services/harbor.py" in out
    assert "app/services/build.py" not in out
    assert out["app/services/harbor.py"]["killed"] == 4
