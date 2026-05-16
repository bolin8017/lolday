#!/usr/bin/env bash
# Refuse a commit when charts/lolday/helpers.lock disagrees with the
# helper subtrees at HEAD. Used by:
#   - the pre-commit hook (.pre-commit-config.yaml: helpers-lock-fresh)
#   - scripts/deploy.sh's drift guard
#
# Set LOLDAY_SKIP_HELPERS_LOCK_CHECK=1 to bypass (e.g. on a disconnected
# dev machine). The README tells operators when this is acceptable.
#
# Phase 4 D4.2 R6: drift logic lives in scripts/lib/helpers_lock.py;
# this shell file is now pure orchestration.
set -euo pipefail

if [ "${LOLDAY_SKIP_HELPERS_LOCK_CHECK:-0}" = "1" ]; then
  exit 0
fi

# SCRIPT_HOME — always the real repo containing scripts/lib/ (where the
# helpers_lock module lives). REPO_ROOT — the tree whose helpers.lock and
# git HEAD are being checked (overridable for tests via
# LOLDAY_REPO_ROOT_OVERRIDE). These differ when bats runs against a
# fixture repo at $TMP while the helpers_lock module stays in the real
# repo.
SCRIPT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="${LOLDAY_REPO_ROOT_OVERRIDE:-$SCRIPT_HOME}"
LOCK_FILE="$REPO_ROOT/charts/lolday/helpers.lock"

if [ ! -f "$LOCK_FILE" ]; then
  echo "ERROR: $LOCK_FILE missing — run 'bash scripts/build-helpers.sh' first" >&2
  exit 1
fi

PYTHONPATH="$SCRIPT_HOME" python3 -m scripts.lib.helpers_lock check-drift \
  "$LOCK_FILE" --repo "$REPO_ROOT"
