#!/usr/bin/env bash
# Refuse a commit when charts/lolday/helpers.lock disagrees with the
# helper subtrees at HEAD. Used by:
#   - the pre-commit hook (.pre-commit-config.yaml: helpers-lock-fresh)
#   - scripts/deploy.sh's drift guard
#
# Set LOLDAY_SKIP_HELPERS_LOCK_CHECK=1 to bypass (e.g. on a disconnected
# dev machine). The README tells operators when this is acceptable.
set -euo pipefail

if [ "${LOLDAY_SKIP_HELPERS_LOCK_CHECK:-0}" = "1" ]; then
  exit 0
fi

REPO_ROOT="${LOLDAY_REPO_ROOT_OVERRIDE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCK_FILE="$REPO_ROOT/charts/lolday/helpers.lock"

if [ ! -f "$LOCK_FILE" ]; then
  echo "ERROR: $LOCK_FILE missing — run 'bash scripts/build-helpers.sh' first" >&2
  exit 1
fi

drift="$(cd "$REPO_ROOT" && python3 - "$LOCK_FILE" <<'PY'
import json, subprocess, sys
lock = json.load(open(sys.argv[1]))
out = []
for key, ref in lock.items():
    helper = key.replace("_", "-")
    sha = subprocess.check_output(
        ["git", "rev-parse", "--short=12", f"HEAD:charts/lolday/helpers/{helper}"],
        text=True,
    ).strip()
    if not ref.endswith(f":{sha}"):
        out.append(f"  {helper}: lock={ref} HEAD=...:{sha}")
print("\n".join(out))
PY
)"

if [ -n "$drift" ]; then
  {
    echo "ERROR: helpers.lock drift detected:"
    echo "$drift"
    echo "Run 'bash scripts/build-helpers.sh' and commit the updated lock."
  } >&2
  exit 1
fi

exit 0
