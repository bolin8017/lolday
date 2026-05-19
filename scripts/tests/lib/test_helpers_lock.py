"""Phase 4 D4.2 R6 — unit tests for scripts/lib/helpers_lock.py."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.lib import helpers_lock

DIGEST = "@sha256:" + "a" * 64


def _seed_git_repo(repo: Path) -> dict[str, str]:
    """Create a tiny git repo with the two helper subtrees and return
    their 12-char tree SHAs."""
    subprocess.check_call(["git", "-C", str(repo), "init", "-q"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.email", "t@t"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.name", "t"])
    for helper in ("build-helper", "job-helper"):
        sub = repo / "charts" / "lolday" / "helpers" / helper
        sub.mkdir(parents=True)
        (sub / "Dockerfile").write_text(f"FROM alpine\nLABEL helper={helper}\n")
    subprocess.check_call(["git", "-C", str(repo), "add", "-A"])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-qm", "seed"])
    return {
        helper: subprocess.check_output(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--short=12",
                f"HEAD:charts/lolday/helpers/{helper}",
            ],
            text=True,
        ).strip()
        for helper in ("build-helper", "job-helper")
    }


def test_write_lock_creates_pretty_sorted_json(tmp_path: Path) -> None:
    lock = tmp_path / "helpers.lock"
    helpers_lock.write_lock(
        lock,
        "harbor.example/build-helper:abc" + DIGEST,
        "harbor.example/job-helper:def" + DIGEST,
    )
    text = lock.read_text()
    assert text.endswith("\n")
    parsed = json.loads(text)
    assert parsed["build_helper"].endswith(DIGEST)
    assert parsed["job_helper"].endswith(DIGEST)
    assert text.index("build_helper") < text.index("job_helper")


def test_write_lock_leaves_no_partial_tmp(tmp_path: Path) -> None:
    lock = tmp_path / "helpers.lock"
    helpers_lock.write_lock(lock, "a" + DIGEST, "b" + DIGEST)
    leftovers = list(tmp_path.glob("helpers.lock.*.tmp"))
    assert leftovers == []


def test_read_lock_roundtrip(tmp_path: Path) -> None:
    lock = tmp_path / "helpers.lock"
    helpers_lock.write_lock(lock, "X" + DIGEST, "Y" + DIGEST)
    data = helpers_lock.read_lock(lock)
    assert data == {"build_helper": "X" + DIGEST, "job_helper": "Y" + DIGEST}


def test_read_lock_raises_on_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        helpers_lock.read_lock(tmp_path / "nope.lock")


def test_read_lock_rejects_non_object_payload(tmp_path: Path) -> None:
    lock = tmp_path / "helpers.lock"
    lock.write_text("[1, 2, 3]")
    with pytest.raises(ValueError, match="not a JSON object"):
        helpers_lock.read_lock(lock)


def test_check_drift_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shas = _seed_git_repo(repo)
    lock = repo / "charts" / "lolday" / "helpers.lock"
    helpers_lock.write_lock(
        lock,
        f"harbor.lolday.svc:80/lolday/build-helper:{shas['build-helper']}{DIGEST}",
        f"harbor.lolday.svc:80/lolday/job-helper:{shas['job-helper']}{DIGEST}",
    )
    assert helpers_lock.check_drift(lock, repo_root=repo) == []


def test_check_drift_detects_sha_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_git_repo(repo)
    lock = repo / "charts" / "lolday" / "helpers.lock"
    helpers_lock.write_lock(
        lock,
        "harbor.lolday.svc:80/lolday/build-helper:000000000000" + DIGEST,
        "harbor.lolday.svc:80/lolday/job-helper:000000000000" + DIGEST,
    )
    drift = helpers_lock.check_drift(lock, repo_root=repo)
    assert len(drift) == 2
    assert any("build-helper" in line for line in drift)


def test_check_drift_detects_missing_digest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shas = _seed_git_repo(repo)
    lock = repo / "charts" / "lolday" / "helpers.lock"
    helpers_lock.write_lock(
        lock,
        f"harbor.lolday.svc:80/lolday/build-helper:{shas['build-helper']}",
        f"harbor.lolday.svc:80/lolday/job-helper:{shas['job-helper']}",
    )
    drift = helpers_lock.check_drift(lock, repo_root=repo)
    assert all("missing @sha256" in line for line in drift)
    assert len(drift) == 2


# ---------------------------------------------------------------------------
# _dispatch / main — the CLI bridge invoked by build-helpers.sh and
# check-helpers-lock.sh. The library functions above are well-covered; the
# CLI plumbing is what bash actually calls, so the tests below pin its
# usage / exit-code contract so a future refactor cannot silently regress
# (e.g. a 1 vs 2 swap would change deploy.sh's || branch).
# ---------------------------------------------------------------------------


def test_dispatch_no_args_returns_2_with_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert helpers_lock._dispatch([]) == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower()


def test_dispatch_unknown_verb_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert helpers_lock._dispatch(["wat"]) == 2
    assert "unknown verb" in capsys.readouterr().err


def test_dispatch_read_prints_both_helper_refs_in_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = tmp_path / "helpers.lock"
    helpers_lock.write_lock(lock, "B" + DIGEST, "J" + DIGEST)
    assert helpers_lock._dispatch(["read", str(lock)]) == 0
    out = capsys.readouterr().out.splitlines()
    # Convention from helpers_lock.HELPER_KEYS: build_helper first, then job_helper.
    assert out == ["B" + DIGEST, "J" + DIGEST]


def test_dispatch_read_without_path_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert helpers_lock._dispatch(["read"]) == 2
    assert "usage: read" in capsys.readouterr().err


def test_dispatch_read_missing_file_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert helpers_lock._dispatch(["read", str(tmp_path / "nope.lock")]) == 2
    assert "missing" in capsys.readouterr().err.lower()


def test_dispatch_read_malformed_json_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = tmp_path / "helpers.lock"
    lock.write_text("{not json")
    assert helpers_lock._dispatch(["read", str(lock)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_dispatch_write_round_trips(tmp_path: Path) -> None:
    lock = tmp_path / "helpers.lock"
    rc = helpers_lock._dispatch(
        ["write", str(lock), "ref-build" + DIGEST, "ref-job" + DIGEST]
    )
    assert rc == 0
    data = helpers_lock.read_lock(lock)
    assert data == {
        "build_helper": "ref-build" + DIGEST,
        "job_helper": "ref-job" + DIGEST,
    }


def test_dispatch_write_wrong_arg_count_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert helpers_lock._dispatch(["write", "path", "only-one"]) == 2
    assert "usage: write" in capsys.readouterr().err


def test_dispatch_check_drift_clean_returns_0(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shas = _seed_git_repo(repo)
    lock = repo / "charts" / "lolday" / "helpers.lock"
    helpers_lock.write_lock(
        lock,
        f"harbor.lolday.svc:80/lolday/build-helper:{shas['build-helper']}{DIGEST}",
        f"harbor.lolday.svc:80/lolday/job-helper:{shas['job-helper']}{DIGEST}",
    )
    rc = helpers_lock._dispatch(["check-drift", str(lock), "--repo", str(repo)])
    assert rc == 0


def test_dispatch_check_drift_drift_returns_1_and_emits_help(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_git_repo(repo)
    lock = repo / "charts" / "lolday" / "helpers.lock"
    helpers_lock.write_lock(
        lock,
        "harbor.lolday.svc:80/lolday/build-helper:000000000000" + DIGEST,
        "harbor.lolday.svc:80/lolday/job-helper:000000000000" + DIGEST,
    )
    rc = helpers_lock._dispatch(["check-drift", str(lock), "--repo", str(repo)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "drift detected" in err
    # Bash || branch in build-helpers.sh keys off the "Run 'bash …'" hint.
    assert "bash scripts/build-helpers.sh" in err


def test_dispatch_check_drift_repo_root_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no --repo flag is given, the dispatcher reads LOLDAY_REPO_ROOT_OVERRIDE
    or REPO_ROOT from the environment (in that order). build-helpers.sh sets
    REPO_ROOT, so an env-only path must work end-to-end."""
    repo = tmp_path / "repo"
    repo.mkdir()
    shas = _seed_git_repo(repo)
    lock = repo / "charts" / "lolday" / "helpers.lock"
    helpers_lock.write_lock(
        lock,
        f"harbor.lolday.svc:80/lolday/build-helper:{shas['build-helper']}{DIGEST}",
        f"harbor.lolday.svc:80/lolday/job-helper:{shas['job-helper']}{DIGEST}",
    )
    monkeypatch.delenv("LOLDAY_REPO_ROOT_OVERRIDE", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(repo))
    assert helpers_lock._dispatch(["check-drift", str(lock)]) == 0


def test_dispatch_check_drift_missing_lock_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = helpers_lock._dispatch(
        ["check-drift", str(tmp_path / "nope.lock"), "--repo", str(tmp_path)]
    )
    assert rc == 2
    assert "missing" in capsys.readouterr().err.lower()


def test_main_with_no_argv_reads_sys_argv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main(None)`` (the entrypoint shape used by ``python -m scripts.lib.helpers_lock``)
    must defer to sys.argv. Pinned so a future refactor cannot break the
    bash-side invocation pattern silently."""
    monkeypatch.setattr("sys.argv", ["helpers_lock"])
    assert helpers_lock.main() == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_write_lock_cleans_up_tmp_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If os.replace raises after a tmp file has been fsynced, write_lock
    must unlink the tmp so the next invocation doesn't accumulate
    ``helpers.lock.*.tmp`` leftovers (and the original lock stays intact)."""
    lock = tmp_path / "helpers.lock"
    lock.write_text('{"build_helper": "old", "job_helper": "old"}\n')

    def _boom(_src: str, _dst: Path | str) -> None:
        raise OSError("simulated atomic-replace failure")

    monkeypatch.setattr(helpers_lock.os, "replace", _boom)
    with pytest.raises(OSError, match="simulated atomic-replace failure"):
        helpers_lock.write_lock(lock, "B" + DIGEST, "J" + DIGEST)

    assert list(tmp_path.glob("helpers.lock.*.tmp")) == []
    # Original lock untouched (atomic guarantee).
    assert json.loads(lock.read_text())["build_helper"] == "old"
