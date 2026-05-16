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
