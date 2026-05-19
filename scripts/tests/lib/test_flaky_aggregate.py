"""Phase 1 D1.13 — unit tests for scripts/lib/flaky_aggregate.py.

R6 follow-up (`.claude/rules/scripts-and-ops.md` §R6): every
`scripts/lib/*.py` module needs a pytest unit. flaky_aggregate.py was
listed as an "existing extraction precedent" alongside harbor_api /
helpers_lock / mutation_report / test_telemetry — all of which had units
shipped in Phase 4 D4.2-D4.4. This file fills the gap.

Coverage scope:
- `parse_runs` — JUnit XML walker (the data-mining surface that any flaky
  detection downstream depends on). Cover the happy path, the multi-file
  aggregate path, the malformed-XML skip path, the failure/error
  classification, and the empty-dir edge case.
- `main` — argv handling (missing arg, non-existent dir, dry-run path).
  GitHub-API call (`open_issue`) is exercised via the dry-run env flag
  so no real `gh` subprocess fires in CI.

`open_issue` itself is not unit-tested — it's a thin wrapper around
`subprocess.run(["gh", "issue", "create", ...])` with no branching logic;
a meaningful test would require either a `gh` mock or a fake GH API
listener, and the cost outweighs the value for a six-line wrapper. The
dry-run path covers the call-site gating.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scripts.lib import flaky_aggregate


def _write_junit(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).strip(), encoding="utf-8")


@pytest.fixture
def two_run_artifact_dir(tmp_path: Path) -> Path:
    """Two JUnit XML files from a hypothetical two-day window.

    `fast_a` passes both runs → stable.
    `flake_b` passes once, fails once → 50 % rate, above THRESHOLD when
              the synthetic MIN_RUNS bar is lowered in the calling test.
    `error_c` errors once, passes once → also tracked as a fail.
    """
    art = tmp_path / "artifacts"
    art.mkdir()
    _write_junit(
        art / "junit-day1.xml",
        """
        <testsuites>
          <testsuite name="suite1">
            <testcase classname="m.x" name="fast_a"/>
            <testcase classname="m.x" name="flake_b">
              <failure message="boom">stacktrace</failure>
            </testcase>
            <testcase classname="m.x" name="error_c">
              <error message="ImportError"/>
            </testcase>
          </testsuite>
        </testsuites>
        """,
    )
    _write_junit(
        art / "junit-day2.xml",
        """
        <testsuites>
          <testsuite name="suite1">
            <testcase classname="m.x" name="fast_a"/>
            <testcase classname="m.x" name="flake_b"/>
            <testcase classname="m.x" name="error_c"/>
          </testsuite>
        </testsuites>
        """,
    )
    return art


def test_parse_runs_counts_pass_fail_and_error(two_run_artifact_dir: Path) -> None:
    """`error` elements are counted as failures alongside `failure`.

    The flaky-tracker decision contract: any failed-or-errored test
    counts toward the fail-rate. A test that ImportErrors is just as
    flaky as one that asserts wrong.
    """
    stats = flaky_aggregate.parse_runs(two_run_artifact_dir)
    # Three distinct test names, each seen twice.
    assert {"m.x::fast_a", "m.x::flake_b", "m.x::error_c"} == set(stats)
    assert stats["m.x::fast_a"] == {"total": 2, "fail": 0}
    assert stats["m.x::flake_b"] == {"total": 2, "fail": 1}
    assert stats["m.x::error_c"] == {"total": 2, "fail": 1}


def test_parse_runs_walks_subdirectories(tmp_path: Path) -> None:
    """`rglob("junit*.xml")` reaches nested artifact layouts (e.g.
    `artifacts/<run-id>/junit-<job>.xml` as produced by
    `actions/upload-artifact`).
    """
    art = tmp_path / "artifacts"
    (art / "run-123" / "backend").mkdir(parents=True)
    (art / "run-124").mkdir()
    _write_junit(
        art / "run-123" / "backend" / "junit-fast.xml",
        """
        <testsuites>
          <testsuite>
            <testcase classname="a" name="t1"/>
          </testsuite>
        </testsuites>
        """,
    )
    _write_junit(
        art / "run-124" / "junit-frontend.xml",
        """
        <testsuites>
          <testsuite>
            <testcase classname="b" name="t2">
              <failure/>
            </testcase>
          </testsuite>
        </testsuites>
        """,
    )
    stats = flaky_aggregate.parse_runs(art)
    assert stats["a::t1"] == {"total": 1, "fail": 0}
    assert stats["b::t2"] == {"total": 1, "fail": 1}


def test_parse_runs_skips_malformed_xml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A corrupt artifact must not crash the aggregator — just warn and
    move on. Otherwise one bad XML file from one worker kills the entire
    weekly tracker for everyone.
    """
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "junit-bad.xml").write_text("<not really xml", encoding="utf-8")
    _write_junit(
        art / "junit-good.xml",
        """
        <testsuites>
          <testsuite>
            <testcase classname="x" name="t1"/>
          </testsuite>
        </testsuites>
        """,
    )
    stats = flaky_aggregate.parse_runs(art)
    assert stats == {"x::t1": {"total": 1, "fail": 0}}
    captured = capsys.readouterr()
    assert "skipping malformed XML" in captured.err
    assert "junit-bad.xml" in captured.err


def test_parse_runs_returns_empty_for_empty_dir(tmp_path: Path) -> None:
    """Empty `artifact_dir` (week with zero workflow runs) returns an
    empty stats dict — `main` then prints "Analyzed 0 unique tests".
    """
    art = tmp_path / "artifacts"
    art.mkdir()
    assert flaky_aggregate.parse_runs(art) == {}


def test_parse_runs_skips_non_junit_xml(tmp_path: Path) -> None:
    """`rglob("junit*.xml")` only matches `junit*.xml` — other XML
    artifacts (helm rendered manifests, kubeconform output) are ignored.
    """
    art = tmp_path / "artifacts"
    art.mkdir()
    # Different name → not picked up by the glob.
    _write_junit(
        art / "helm-render.xml",
        """
        <testsuites><testsuite><testcase classname='helm' name='r'/></testsuite></testsuites>
        """,
    )
    _write_junit(
        art / "junit-real.xml",
        """
        <testsuites><testsuite><testcase classname='real' name='t'/></testsuite></testsuites>
        """,
    )
    stats = flaky_aggregate.parse_runs(art)
    assert "real::t" in stats
    assert "helm::r" not in stats


def test_main_returns_2_on_missing_arg(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = flaky_aggregate.main(["flaky_aggregate.py"])
    assert rc == 2
    assert "usage:" in capsys.readouterr().err


def test_main_returns_1_on_nonexistent_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does-not-exist"
    rc = flaky_aggregate.main(["flaky_aggregate.py", str(missing)])
    assert rc == 1
    assert "does not exist" in capsys.readouterr().err


def test_main_flaky_detection_in_dry_run(
    two_run_artifact_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: with FLAKY_DRY_RUN=1 the tool reports flagged tests
    but does NOT shell out to `gh issue create`.

    Reason: the file-level `MIN_RUNS` constant is read from env at
    import time so we lower it via monkeypatch on the module attr.
    `THRESHOLD` 0.01 catches the 50% `flake_b` + `error_c` lines.
    """
    monkeypatch.setattr(flaky_aggregate, "MIN_RUNS", 2)
    monkeypatch.setenv("FLAKY_DRY_RUN", "1")
    rc = flaky_aggregate.main(["flaky_aggregate.py", str(two_run_artifact_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 flagged as flaky" in out
    assert "m.x::flake_b" in out
    assert "m.x::error_c" in out
    # `fast_a` was 0/2 — must NOT be flagged.
    assert "m.x::fast_a" not in out.split("flagged as flaky")[1]
    # Dry-run banner appears for each flagged test.
    assert "(dry run; no issue created)" in out


def test_main_dry_run_filters_below_min_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """MIN_RUNS guards against flagging a test that ran only once and
    happened to fail — that's not "flaky", that's "broken". The default
    bar is 10 runs; we raise it artificially to prove the gate works.
    """
    art = tmp_path / "artifacts"
    art.mkdir()
    _write_junit(
        art / "junit-1.xml",
        """
        <testsuites>
          <testsuite>
            <testcase classname='x' name='one_off'>
              <failure/>
            </testcase>
          </testsuite>
        </testsuites>
        """,
    )
    monkeypatch.setattr(flaky_aggregate, "MIN_RUNS", 5)
    monkeypatch.setenv("FLAKY_DRY_RUN", "1")
    rc = flaky_aggregate.main(["flaky_aggregate.py", str(art)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 flagged as flaky" in out
