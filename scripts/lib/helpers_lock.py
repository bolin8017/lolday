"""charts/lolday/helpers.lock JSON read/write/drift-check helpers.

Phase 4 D4.2 R6 part 4 of 6. Extracts the lock-file logic out of
scripts/build-helpers.sh::write_lock and scripts/check-helpers-lock.sh.

Shell callers use:

    python3 -m scripts.lib.helpers_lock <verb> [args...]

verbs:
    read <path>                    — print JSON value of build_helper
                                     (one per line: build_helper,job_helper)
    write <path> <build> <job>     — atomically write a fresh lock JSON
    check-drift <path> [--repo R]  — compare lock entries against HEAD
                                     subtree SHAs; exit 0 clean,
                                     1 drift, 2 io-error

The lock format is the same one currently committed at
charts/lolday/helpers.lock:

    {
      "build_helper": "harbor.lolday.svc:80/lolday/build-helper:<sha12>@sha256:<64hex>",
      "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:<sha12>@sha256:<64hex>"
    }

The check-drift verb encodes H-21-img (every entry must carry the
@sha256:<hex> digest pin) and the existing tag-SHA-matches-HEAD
invariant.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
HELPER_KEYS = ("build_helper", "job_helper")


def read_lock(path: str | Path) -> dict[str, str]:
    """Load the JSON lock file. Raises FileNotFoundError if absent."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"helpers.lock at {path!r} is not a JSON object")
    return data


def write_lock(path: str | Path, build_ref: str, job_ref: str) -> None:
    """Atomically (tmp + rename) write a fresh lock file with the two
    helper refs, pretty-printed with sorted keys."""
    payload = {"build_helper": build_ref, "job_helper": job_ref}
    p = Path(path)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=p.parent,
            prefix=p.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_name = tmp.name
            json.dump(payload, tmp, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, p)
    except Exception:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _git_subtree_sha(repo: Path, helper_name: str) -> str:
    """Return the 12-char tree SHA for charts/lolday/helpers/<helper> at HEAD."""
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "rev-parse",
            "--short=12",
            f"HEAD:charts/lolday/helpers/{helper_name}",
        ],
        text=True,
    ).strip()


def check_drift(lock_path: str | Path, *, repo_root: str | Path) -> list[str]:
    """Compare lock entries against HEAD subtree SHAs. Returns a list
    of human-readable drift messages (empty list = clean)."""
    lock = read_lock(lock_path)
    drift: list[str] = []
    for key, ref in lock.items():
        helper = key.replace("_", "-")
        sha = _git_subtree_sha(Path(repo_root), helper)
        ref_no_digest = DIGEST_RE.sub("", ref)
        if not ref_no_digest.endswith(f":{sha}"):
            drift.append(f"  {helper}: lock={ref} HEAD=...:{sha}")
        if not DIGEST_RE.search(ref):
            drift.append(f"  {helper}: missing @sha256:<64-hex> digest pin: {ref}")
    return drift


# --- CLI dispatch -----------------------------------------------------


def _dispatch(argv: list[str]) -> int:
    if not argv:
        print(
            "usage: python -m scripts.lib.helpers_lock <verb> [args...]",
            file=sys.stderr,
        )
        return 2
    verb, *args = argv
    try:
        if verb == "read":
            if not args:
                print("usage: read <path>", file=sys.stderr)
                return 2
            data = read_lock(args[0])
            for key in HELPER_KEYS:
                print(data.get(key, ""))
        elif verb == "write":
            if len(args) != 3:
                print("usage: write <path> <build_ref> <job_ref>", file=sys.stderr)
                return 2
            write_lock(args[0], args[1], args[2])
        elif verb == "check-drift":
            lock_path = args[0] if args else "charts/lolday/helpers.lock"
            repo_root = (
                os.environ.get("LOLDAY_REPO_ROOT_OVERRIDE")
                or os.environ.get("REPO_ROOT")
                or "."
            )
            if "--repo" in args:
                i = args.index("--repo")
                repo_root = args[i + 1]
            drift = check_drift(lock_path, repo_root=repo_root)
            if drift:
                print("ERROR: helpers.lock drift detected:", file=sys.stderr)
                for line in drift:
                    print(line, file=sys.stderr)
                print(
                    "Run 'bash scripts/build-helpers.sh' and commit the updated lock.",
                    file=sys.stderr,
                )
                return 1
        else:
            print(f"unknown verb: {verb}", file=sys.stderr)
            return 2
    except FileNotFoundError as e:
        print(f"ERROR: helpers.lock missing: {e}", file=sys.stderr)
        return 2
    except (ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    return _dispatch(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
