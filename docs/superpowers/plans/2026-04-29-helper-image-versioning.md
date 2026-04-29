# Helper Image Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hand-pinned `:v3` / `:v4` helper image tags with content-addressable subtree SHAs, an idempotent build-and-push script, and a git-tracked `helpers.lock` consumed by `scripts/deploy.sh`.

**Architecture:** Each helper image is tagged with the first 12 hex digits of its source subtree's git tree SHA (`git rev-parse HEAD:<path>`). A new `scripts/build-helpers.sh` builds, pushes idempotently against Harbor, and writes `charts/lolday/helpers.lock` (JSON). `scripts/deploy.sh` reads the lock, drift-guards it against the current HEAD, and injects the image refs via Helm `--set`. Production fail-fast happens via a Pydantic model_validator that mirrors the existing `validate_sso_config`. A pre-commit hook blocks commits that would leave the lock out of sync.

**Tech Stack:** Bash 5+, kubectl + docker (host-side), Helm 3, Python 3.12 (Pydantic Settings), pytest + monkeypatch, pre-commit framework, Harbor REST API (v2.0).

**Spec:** `docs/superpowers/specs/2026-04-29-helper-image-versioning-design.md`

---

## File Structure

### Created

| Path                                      | Purpose                                                                                                                                        |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/build-helpers.sh`                | Compute subtree SHA, idempotently build/push helper images to Harbor, write `helpers.lock` atomically.                                         |
| `scripts/check-helpers-lock.sh`           | Pre-commit / drift-guard helper. Asserts the lock SHAs match the current HEAD subtree SHAs.                                                    |
| `charts/lolday/helpers.lock`              | JSON lockfile mapping each helper key (snake_case) to a fully-qualified image ref.                                                             |
| `tests/build-helpers/_lib.sh`             | Shell test helpers (`pass`, `fail`, fixture-repo setup).                                                                                       |
| `tests/build-helpers/run_all.sh`          | Test runner; invokes every `test_*.sh` under the directory and reports pass/fail.                                                              |
| `tests/build-helpers/test_compute_sha.sh` | Verifies `compute_sha` returns 12 hex digits and is stable across runs.                                                                        |
| `tests/build-helpers/test_check_clean.sh` | Verifies dirty-tree detection (uncommitted modifications + untracked files).                                                                   |
| `tests/build-helpers/test_dry_run.sh`     | Verifies `--dry-run` prints expected SHAs without invoking docker/kubectl.                                                                     |
| `tests/build-helpers/test_allow_dirty.sh` | Verifies `--allow-dirty` produces `<sha>-dirty-<ts>` tag and does NOT write the lock.                                                          |
| `tests/build-helpers/test_only_flag.sh`   | Verifies `--only NAME` only computes/prints that helper's SHA.                                                                                 |
| `tests/build-helpers/test_lock_format.sh` | Verifies `write_lock` produces valid JSON with the expected snake_case keys and trailing newline.                                              |
| `tests/build-helpers/test_check_lock.sh`  | Verifies `scripts/check-helpers-lock.sh` exits 0 when in sync, exits 1 with diff when drifted, and honours `LOLDAY_SKIP_HELPERS_LOCK_CHECK=1`. |
| `docs/runbooks/release-helpers.md`        | Operator runbook for the helper-image release flow.                                                                                            |

### Modified

| Path                                      | Change                                                                                                                                                                |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.pre-commit-config.yaml`                 | Add `helpers-lock-fresh` local hook.                                                                                                                                  |
| `charts/lolday/.helmignore`               | Add `helpers.lock` line.                                                                                                                                              |
| `backend/app/config.py`                   | `BUILD_IMAGE_HELPER` / `JOB_HELPER_IMAGE` defaults become `""`; new `validate_helper_images` model_validator.                                                         |
| `backend/tests/test_config_validation.py` | Add four `validate_helper_images` test cases.                                                                                                                         |
| `charts/lolday/values.yaml`               | Delete `backend.env.BUILD_IMAGE_HELPER`, `backend.env.JOB_HELPER_IMAGE`, `jobs.helperImage`.                                                                          |
| `scripts/deploy.sh`                       | Add lock-read + drift-guard; add two `--set backend.env.*` lines.                                                                                                     |
| `scripts/recover-harbor.sh`               | Drop the `build-helper` / `job-helper` `build_push` / `skip_if_missing` lines; tail-call `scripts/build-helpers.sh` if the lock exists, else print next-step warning. |
| `README.md`                               | Setup section gains step 5 (`bash scripts/build-helpers.sh`) before the second `bash scripts/deploy.sh` round.                                                        |
| `CLAUDE.md` (root)                        | Quickstart commands gain `bash scripts/build-helpers.sh` line.                                                                                                        |
| `docs/runbooks/deploy.md`                 | §5 cross-references the bootstrap order; mentions the lock + drift-guard exit codes.                                                                                  |
| `docs/architecture.md`                    | §9 #4 + #8 marked **resolved 2026-04-29**.                                                                                                                            |
| `.claude/rules/charts-and-helm.md`        | Helper images section rewritten: tags are SHAs from `helpers.lock`.                                                                                                   |
| `.claude/rules/scripts-and-ops.md`        | Inventory gains `build-helpers.sh`; document the dirty-tree rule and `--allow-dirty` etiquette.                                                                       |

---

## Task 1: Test scaffolding (shared helpers + runner)

**Files:**

- Create: `tests/build-helpers/_lib.sh`
- Create: `tests/build-helpers/run_all.sh`

- [ ] **Step 1: Create the shared test helper library**

```bash
mkdir -p tests/build-helpers
```

Write `tests/build-helpers/_lib.sh`:

```bash
#!/usr/bin/env bash
# Shared helpers for tests/build-helpers/*.sh.
# Provides pass/fail logging plus a `make_fixture_repo` helper that
# materialises an isolated git repo with two helper subtrees.
#
# Tests source this file and consume:
#   - REPO_ROOT  : absolute path of the lolday repo (the script under test)
#   - SCRIPT     : absolute path of scripts/build-helpers.sh
#   - mk_fixture : function returning the path to a freshly-built fixture
#                  repo (caller cd's into it).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/build-helpers.sh"
CHECK_SCRIPT="$REPO_ROOT/scripts/check-helpers-lock.sh"

pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# Build a throwaway git repo that mirrors the relevant slice of the
# lolday tree (charts/lolday/helpers/{build-helper,job-helper}). The
# subtree contents are intentionally tiny — tests care about SHA
# stability, not Dockerfile correctness.
mk_fixture() {
  local dir
  dir="$(mktemp -d)"
  (
    cd "$dir"
    git init -q -b main
    git config user.email "test@lolday.dev"
    git config user.name "Lolday Test"
    mkdir -p charts/lolday/helpers/build-helper
    mkdir -p charts/lolday/helpers/job-helper
    printf 'FROM python:3.12-slim\n' > charts/lolday/helpers/build-helper/Dockerfile
    printf 'placeholder\n'           > charts/lolday/helpers/build-helper/maldet_validator.py
    printf 'FROM python:3.12-slim\n' > charts/lolday/helpers/job-helper/Dockerfile
    printf 'placeholder\n'           > charts/lolday/helpers/job-helper/main.py
    git add -A
    git commit -q -m "initial fixture"
  )
  echo "$dir"
}

# Print the expected 12-char subtree SHA from the fixture repo.
expected_sha() {
  local repo=$1 helper=$2
  ( cd "$repo" && git rev-parse --short=12 "HEAD:charts/lolday/helpers/$helper" )
}
```

Make it readable:

```bash
chmod 0644 tests/build-helpers/_lib.sh
```

- [ ] **Step 2: Create the test runner**

Write `tests/build-helpers/run_all.sh`:

```bash
#!/usr/bin/env bash
# Run every test_*.sh in this directory, report pass/fail, exit non-zero
# on the first failure. Run with `bash tests/build-helpers/run_all.sh`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
shopt -s nullglob

failed=0
for t in "$SCRIPT_DIR"/test_*.sh; do
  printf '\n--- %s ---\n' "$(basename "$t")"
  if bash "$t"; then
    :
  else
    failed=$((failed + 1))
  fi
done

if [ "$failed" -gt 0 ]; then
  printf '\n\033[31m%d test file(s) failed\033[0m\n' "$failed" >&2
  exit 1
fi
printf '\n\033[32mAll build-helpers tests passed\033[0m\n'
```

```bash
chmod +x tests/build-helpers/run_all.sh
```

- [ ] **Step 3: Smoke-run the empty runner**

Run: `bash tests/build-helpers/run_all.sh`
Expected: prints `All build-helpers tests passed` (the `nullglob` makes the empty for-loop a no-op).

- [ ] **Step 4: Commit**

```bash
git add tests/build-helpers/_lib.sh tests/build-helpers/run_all.sh
git commit -m "test(helpers): add shell test scaffolding for build-helpers"
```

---

## Task 2: TDD `compute_sha`

**Files:**

- Create: `tests/build-helpers/test_compute_sha.sh`
- Create: `scripts/build-helpers.sh` (skeleton + `compute_sha`)

- [ ] **Step 1: Write the failing test**

Write `tests/build-helpers/test_compute_sha.sh`:

```bash
#!/usr/bin/env bash
# compute_sha NAME prints the first 12 hex chars of the subtree's tree
# SHA at HEAD. Stable across calls; differs when the subtree changes.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

# Source the script under test. The script must expose compute_sha
# without running main(); guard it via $LOLDAY_BUILD_HELPERS_SOURCED.
LOLDAY_BUILD_HELPERS_SOURCED=1
# shellcheck disable=SC1090
. "$SCRIPT"

# Stability + format
got_a="$(compute_sha build-helper)"
got_b="$(compute_sha build-helper)"
[ "$got_a" = "$got_b" ] || fail "compute_sha not stable across calls ($got_a vs $got_b)"
[[ "$got_a" =~ ^[0-9a-f]{12}$ ]] || fail "compute_sha did not return 12 hex chars (got '$got_a')"
pass "compute_sha is stable + 12-hex format"

# SHA matches git rev-parse directly
expected="$(expected_sha "$repo" build-helper)"
[ "$got_a" = "$expected" ] || fail "compute_sha drift vs git rev-parse ($got_a vs $expected)"
pass "compute_sha matches git rev-parse"

# Different helper → different SHA
job_sha="$(compute_sha job-helper)"
[ "$job_sha" != "$got_a" ] || fail "build-helper and job-helper share SHA — fixture broken"
pass "different helpers produce different SHAs"

# Subtree mutation → SHA changes
echo "// extra" >> charts/lolday/helpers/build-helper/maldet_validator.py
git add -A
git commit -q -m "mutate build-helper"
new_sha="$(compute_sha build-helper)"
[ "$new_sha" != "$got_a" ] || fail "subtree mutation did not change SHA"
pass "subtree mutation shifts SHA"
```

```bash
chmod +x tests/build-helpers/test_compute_sha.sh
```

- [ ] **Step 2: Run the test, watch it fail**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: fails with `bash: <SCRIPT>: No such file or directory`.

- [ ] **Step 3: Implement the script skeleton + `compute_sha`**

Write `scripts/build-helpers.sh`:

```bash
#!/usr/bin/env bash
# Build, push, and pin lolday helper images. Each image is tagged with
# its source subtree's 12-char tree SHA at HEAD; identical subtree
# content yields identical tags so the rebuild is idempotent.
#
# Usage:
#   bash scripts/build-helpers.sh [--allow-dirty] [--dry-run] [--only NAME]
#
# See docs/superpowers/specs/2026-04-29-helper-image-versioning-design.md
# and docs/runbooks/release-helpers.md for the design + operator flow.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_FILE="$REPO_ROOT/charts/lolday/helpers.lock"
HARBOR_HOST_PUSH=harbor.lolday.svc.cluster.local:80   # docker push target (host /etc/hosts)
HARBOR_HOST_REF=harbor.lolday.svc:80                  # value embedded in image refs
HARBOR_PROJECT=lolday
HELPERS=(build-helper job-helper)

# ----- pure helpers ----------------------------------------------------

# compute_sha NAME — print the first 12 hex chars of the subtree's tree
# SHA at HEAD. Aborts if the path is missing in HEAD.
compute_sha() {
  local name=$1
  ( cd "$REPO_ROOT" && \
    git rev-parse --short=12 "HEAD:charts/lolday/helpers/$name" )
}

# (more functions added in subsequent tasks)

# ----- entrypoint ------------------------------------------------------

# Allow `source scripts/build-helpers.sh` from tests without firing main.
if [ -z "${LOLDAY_BUILD_HELPERS_SOURCED:-}" ]; then
  echo "build-helpers.sh: not yet implemented (skeleton)" >&2
  exit 2
fi
```

```bash
chmod +x scripts/build-helpers.sh
```

- [ ] **Step 4: Run the test, watch it pass**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: `--- test_compute_sha.sh ---` block reports four passes; runner exits 0.

- [ ] **Step 5: Commit**

```bash
git add tests/build-helpers/test_compute_sha.sh scripts/build-helpers.sh
git commit -m "feat(helpers): add compute_sha skeleton in build-helpers.sh"
```

---

## Task 3: TDD `check_clean` (dirty + shallow detection)

**Files:**

- Create: `tests/build-helpers/test_check_clean.sh`
- Modify: `scripts/build-helpers.sh` (add `check_clean`, `assert_not_shallow`)

- [ ] **Step 1: Write the failing test**

Write `tests/build-helpers/test_check_clean.sh`:

```bash
#!/usr/bin/env bash
# check_clean NAME exits 0 on a clean subtree and 1 on either an
# uncommitted modification or an untracked file inside it. Shallow-clone
# detection lives in assert_not_shallow.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

LOLDAY_BUILD_HELPERS_SOURCED=1
# Override REPO_ROOT so the sourced script targets the fixture, not the
# real lolday repo.
REPO_ROOT="$repo"
# shellcheck disable=SC1090
. "$SCRIPT"

# Clean tree → exit 0
if check_clean build-helper; then
  pass "clean subtree accepted"
else
  fail "clean subtree rejected"
fi

# Modify a tracked file → exit 1
echo "// dirty" >> charts/lolday/helpers/build-helper/maldet_validator.py
if check_clean build-helper 2>/dev/null; then
  fail "modified subtree accepted (should be rejected)"
fi
pass "modified subtree rejected"

# Roll back, add an untracked file → exit 1
git checkout -- charts/lolday/helpers/build-helper/maldet_validator.py
touch charts/lolday/helpers/build-helper/UNTRACKED.tmp
if check_clean build-helper 2>/dev/null; then
  fail "subtree with untracked file accepted (should be rejected)"
fi
pass "untracked file in subtree rejected"
rm charts/lolday/helpers/build-helper/UNTRACKED.tmp

# Shallow-clone detection (independent of subtree state).
shallow="$(mktemp -d)"
( cd "$shallow" && git clone --depth=1 -q "file://$repo" clone )
(
  cd "$shallow/clone"
  LOLDAY_BUILD_HELPERS_SOURCED=1
  REPO_ROOT="$shallow/clone"
  # shellcheck disable=SC1090
  . "$SCRIPT"
  if assert_not_shallow 2>/dev/null; then
    exit 1   # signals fail
  else
    exit 0   # expected — shallow refused
  fi
) || fail "assert_not_shallow accepted a shallow clone"
pass "shallow clone rejected"
rm -rf "$shallow"
```

```bash
chmod +x tests/build-helpers/test_check_clean.sh
```

- [ ] **Step 2: Run the test, watch it fail**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: `test_check_clean.sh` fails with `bash: check_clean: command not found` or similar.

- [ ] **Step 3: Add `check_clean` + `assert_not_shallow` to the script**

Edit `scripts/build-helpers.sh` — replace the `# (more functions added in subsequent tasks)` line with:

```bash
# check_clean NAME — exits 0 if the subtree has no uncommitted
# modifications and no untracked files; otherwise prints the offending
# paths to stderr and exits 1.
check_clean() {
  local name=$1
  local path="charts/lolday/helpers/$name"
  ( cd "$REPO_ROOT" && \
      git diff --quiet HEAD -- "$path" ) \
    || { echo "  uncommitted modifications under $path" >&2; return 1; }
  local untracked
  untracked="$( cd "$REPO_ROOT" && \
                git ls-files --others --exclude-standard "$path" )"
  if [ -n "$untracked" ]; then
    echo "  untracked files under $path:" >&2
    printf '    %s\n' $untracked >&2
    return 1
  fi
  return 0
}

# assert_not_shallow — refuses to proceed in a shallow clone, where the
# tree object lookups behind compute_sha would silently fail with
# "missing tree". Prints a remediation message before exiting 1.
assert_not_shallow() {
  local is_shallow
  is_shallow="$( cd "$REPO_ROOT" && git rev-parse --is-shallow-repository )"
  if [ "$is_shallow" = "true" ]; then
    echo "ERROR: shallow clone detected — cannot resolve subtree SHAs." >&2
    echo "       Run 'git fetch --unshallow' and retry." >&2
    return 1
  fi
  return 0
}
```

- [ ] **Step 4: Run the test, watch it pass**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: both `test_compute_sha.sh` and `test_check_clean.sh` pass.

- [ ] **Step 5: Commit**

```bash
git add tests/build-helpers/test_check_clean.sh scripts/build-helpers.sh
git commit -m "feat(helpers): reject dirty subtree and shallow clone in build-helpers"
```

---

## Task 4: TDD `write_lock` (pure JSON serialiser)

**Files:**

- Create: `tests/build-helpers/test_lock_format.sh`
- Modify: `scripts/build-helpers.sh` (add `write_lock`)

- [ ] **Step 1: Write the failing test**

Write `tests/build-helpers/test_lock_format.sh`:

```bash
#!/usr/bin/env bash
# write_lock REF_BUILD_HELPER REF_JOB_HELPER writes JSON with snake_case
# keys, ASCII output, two-space indent, and trailing newline. Atomic:
# the target file is replaced via rename, never partial.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

LOLDAY_BUILD_HELPERS_SOURCED=1
REPO_ROOT="$repo"
LOCK_FILE="$repo/charts/lolday/helpers.lock"
# shellcheck disable=SC1090
. "$SCRIPT"

write_lock \
  "harbor.lolday.svc:80/lolday/build-helper:abc123def456" \
  "harbor.lolday.svc:80/lolday/job-helper:0123456789ab"

[ -f "$LOCK_FILE" ] || fail "lock file not created"

# Last byte must be a newline.
last_byte="$(tail -c1 "$LOCK_FILE" | od -An -tx1 | tr -d ' ')"
[ "$last_byte" = "0a" ] || fail "lock file missing trailing newline"
pass "trailing newline present"

# Parses as JSON, has exactly the two expected keys, and the values
# match what we wrote.
python3 - "$LOCK_FILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
expected = {
    "build_helper": "harbor.lolday.svc:80/lolday/build-helper:abc123def456",
    "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:0123456789ab",
}
assert d == expected, f"unexpected lock contents: {d}"
PY
pass "JSON parses + contains expected keys/values"
```

```bash
chmod +x tests/build-helpers/test_lock_format.sh
```

- [ ] **Step 2: Run, watch it fail**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: fails with `bash: write_lock: command not found`.

- [ ] **Step 3: Implement `write_lock`**

Append to `scripts/build-helpers.sh` (above the entrypoint `if` block):

```bash
# write_lock BUILD_HELPER_REF JOB_HELPER_REF — atomically writes
# helpers.lock as pretty-printed JSON with snake_case keys. Uses python3
# (already a hard dep of deploy.sh) so the output is always valid JSON.
write_lock() {
  local build_ref=$1 job_ref=$2
  local tmp
  tmp="$(mktemp "${LOCK_FILE}.XXXXXX")"
  BUILD_REF="$build_ref" JOB_REF="$job_ref" python3 - "$tmp" <<'PY'
import json, os, sys
out = {
    "build_helper": os.environ["BUILD_REF"],
    "job_helper":   os.environ["JOB_REF"],
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2, sort_keys=True)
    f.write("\n")
PY
  mv "$tmp" "$LOCK_FILE"
}
```

- [ ] **Step 4: Run, watch it pass**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: all three test files pass.

- [ ] **Step 5: Commit**

```bash
git add tests/build-helpers/test_lock_format.sh scripts/build-helpers.sh
git commit -m "feat(helpers): write helpers.lock atomically as pretty JSON"
```

---

## Task 5: Implement Harbor / docker integration

These functions touch real Harbor and docker, so we exercise them through the bootstrap rehearsal at the end of Phase A rather than mocking them in shell tests.

**Files:**

- Modify: `scripts/build-helpers.sh` (add `harbor_login`, `harbor_has_tag`, `docker_build_push`)

- [ ] **Step 1: Add `harbor_login`**

Append to `scripts/build-helpers.sh` (above the entrypoint `if`):

```bash
# harbor_login — pull the robot$build-pusher credentials out of the
# K8s harbor-push-cred Secret, decode the dockerconfigjson, and run
# `docker login`. Stores credentials only in $HOME/.docker/config.json
# (the operator can `docker logout` after to wipe them).
harbor_login() {
  if ! kubectl -n lolday get secret harbor-push-cred >/dev/null 2>&1; then
    echo "ERROR: K8s Secret lolday/harbor-push-cred not found." >&2
    echo "       Run 'bash scripts/recover-harbor.sh' first to bootstrap" >&2
    echo "       Harbor projects + the robot account." >&2
    return 1
  fi
  local cfg auth user secret
  cfg="$(kubectl -n lolday get secret harbor-push-cred \
           -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d)"
  auth="$(python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
# The Secret carries auth for both DNS forms; either decodes to the
# same robot$build-pusher:<secret> tuple. Pick the cluster-DNS form
# because docker login below uses .cluster.local.
print(d["auths"]["harbor.lolday.svc:80"]["auth"])
' <<<"$cfg" | base64 -d)"
  user="${auth%%:*}"
  secret="${auth#*:}"
  echo "$secret" | \
    docker login "$HARBOR_HOST_PUSH" -u "$user" --password-stdin >/dev/null
}

# harbor_has_tag NAME SHA — true (returns 0) if Harbor already serves
# `lolday/<NAME>:<SHA>`. Uses the robot's repository:pull scope so no
# admin password is needed. Curl is used directly (jq isn't a hard dep).
harbor_has_tag() {
  local name=$1 sha=$2
  local cfg auth url status body matches
  cfg="$(kubectl -n lolday get secret harbor-push-cred \
           -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d)"
  auth="$(python3 -c '
import json, sys
print(json.loads(sys.stdin.read())["auths"]["harbor.lolday.svc:80"]["auth"])
' <<<"$cfg")"
  url="http://$HARBOR_HOST_PUSH/api/v2.0/projects/$HARBOR_PROJECT/repositories/$name/artifacts?with_tag=true&q=tags=$sha"
  body="$(mktemp)"
  status="$(curl -sS -o "$body" -w '%{http_code}' \
              -H "Authorization: Basic $auth" "$url" || echo 000)"
  if [ "$status" = "200" ]; then
    # Body is a (possibly empty) JSON array.
    matches="$(python3 - <"$body" <<'PY'
import json, sys
d = json.load(sys.stdin)
print(len(d) if isinstance(d, list) else 0)
PY
)"
    rm -f "$body"
    [ "$matches" -gt 0 ]
  elif [ "$status" = "404" ]; then
    rm -f "$body"
    return 1
  else
    cat "$body" >&2
    rm -f "$body"
    echo "ERROR: harbor_has_tag $name $sha returned HTTP $status" >&2
    return 2
  fi
}

# docker_build_push NAME SHA — build the image from the helper subtree
# and push it to Harbor under lolday/<NAME>:<SHA>. The build context is
# the helper subtree itself (no parent paths).
docker_build_push() {
  local name=$1 sha=$2
  local ref="$HARBOR_HOST_PUSH/$HARBOR_PROJECT/$name:$sha"
  ( cd "$REPO_ROOT" && \
    docker build --pull -t "$ref" "charts/lolday/helpers/$name" )
  docker push "$ref"
}
```

- [ ] **Step 2: Sanity-syntax check**

```bash
bash -n scripts/build-helpers.sh
```

Expected: exit 0, no output (script parses).

- [ ] **Step 3: Run shell tests (regression check)**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: all three earlier tests still pass — the new functions don't run unless invoked.

- [ ] **Step 4: Commit**

```bash
git add scripts/build-helpers.sh
git commit -m "feat(helpers): add Harbor login, idempotency check, and docker push"
```

---

## Task 6: TDD orchestrator (`main` + flag parsing + dry-run + --only)

**Files:**

- Create: `tests/build-helpers/test_dry_run.sh`
- Create: `tests/build-helpers/test_only_flag.sh`
- Create: `tests/build-helpers/test_allow_dirty.sh`
- Modify: `scripts/build-helpers.sh` (add `parse_args`, `main`)

- [ ] **Step 1: Write `test_dry_run.sh`**

```bash
#!/usr/bin/env bash
# --dry-run prints the SHAs for both helpers without touching docker /
# kubectl / Harbor. The output line shape is fixed; downstream tooling
# (CI, README copy/paste) depends on it.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

# Run with --dry-run. We intentionally do NOT define a kubectl mock —
# the script must short-circuit before any kubectl call.
out="$(bash "$SCRIPT" --dry-run 2>&1)"

build_sha="$(expected_sha "$repo" build-helper)"
job_sha="$(expected_sha "$repo" job-helper)"

echo "$out" | grep -qF "build-helper:$build_sha" \
  || fail "dry-run did not print expected build-helper SHA"
pass "dry-run prints build-helper:$build_sha"

echo "$out" | grep -qF "job-helper:$job_sha" \
  || fail "dry-run did not print expected job-helper SHA"
pass "dry-run prints job-helper:$job_sha"

# Lock must NOT be touched in --dry-run.
[ ! -e "$repo/charts/lolday/helpers.lock" ] \
  || fail "dry-run wrote helpers.lock — it must not"
pass "dry-run leaves helpers.lock untouched"
```

```bash
chmod +x tests/build-helpers/test_dry_run.sh
```

- [ ] **Step 2: Write `test_only_flag.sh`**

```bash
#!/usr/bin/env bash
# --only NAME limits the run to a single helper.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

build_sha="$(expected_sha "$repo" build-helper)"
job_sha="$(expected_sha "$repo" job-helper)"

out="$(bash "$SCRIPT" --dry-run --only build-helper 2>&1)"
echo "$out" | grep -qF "build-helper:$build_sha" \
  || fail "--only build-helper missed build-helper output"
echo "$out" | grep -qF "job-helper" \
  && fail "--only build-helper still mentioned job-helper"
pass "--only build-helper isolates to build-helper"

# Unknown helper → exit non-zero
if bash "$SCRIPT" --dry-run --only nope 2>/dev/null; then
  fail "--only nope did not exit non-zero"
fi
pass "--only nope rejected"
```

```bash
chmod +x tests/build-helpers/test_only_flag.sh
```

- [ ] **Step 3: Write `test_allow_dirty.sh`**

```bash
#!/usr/bin/env bash
# --allow-dirty: dirty subtree allowed, tag suffix `-dirty-<ts>`,
# helpers.lock NOT written.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

# Dirty up the tree.
echo "// dirty" >> charts/lolday/helpers/build-helper/maldet_validator.py

# Without --allow-dirty, --dry-run still rejects.
if bash "$SCRIPT" --dry-run 2>/dev/null; then
  fail "default --dry-run accepted dirty subtree"
fi
pass "default mode refuses dirty subtree"

# With --allow-dirty + --dry-run, output contains the -dirty- suffix
# and the lock is not touched.
out="$(bash "$SCRIPT" --dry-run --allow-dirty 2>&1)"
echo "$out" | grep -Eq "build-helper:[0-9a-f]{12}-dirty-[0-9]+" \
  || fail "--allow-dirty did not stamp -dirty-<ts>"
pass "--allow-dirty stamps -dirty-<ts> tag"

[ ! -e "$repo/charts/lolday/helpers.lock" ] \
  || fail "--allow-dirty wrote helpers.lock — must not"
pass "--allow-dirty leaves helpers.lock untouched"
```

```bash
chmod +x tests/build-helpers/test_allow_dirty.sh
```

- [ ] **Step 4: Run all three tests, watch them fail**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: the three new tests fail because the script's entrypoint just prints "skeleton". Earlier tests still pass.

- [ ] **Step 5: Replace the entrypoint with a real `main`**

Replace the entrypoint stub at the bottom of `scripts/build-helpers.sh` with:

```bash
# parse_args — populate the OPT_* / ONLY globals from argv. Unknown args
# exit 1.
parse_args() {
  OPT_DRY_RUN=0
  OPT_ALLOW_DIRTY=0
  ONLY=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run)     OPT_DRY_RUN=1; shift ;;
      --allow-dirty) OPT_ALLOW_DIRTY=1; shift ;;
      --only)
        [ $# -ge 2 ] || { echo "ERROR: --only requires a NAME" >&2; exit 1; }
        ONLY="$2"; shift 2 ;;
      --help|-h)
        cat <<'EOF'
Usage: scripts/build-helpers.sh [--allow-dirty] [--dry-run] [--only NAME]

Build, push, and pin lolday helper images. See
docs/runbooks/release-helpers.md for the operator flow.
EOF
        exit 0 ;;
      *)
        echo "ERROR: unknown flag: $1" >&2
        exit 1 ;;
    esac
  done
}

# resolve_targets — print one helper name per line, honouring --only.
resolve_targets() {
  if [ -n "$ONLY" ]; then
    local found=0
    for h in "${HELPERS[@]}"; do
      if [ "$h" = "$ONLY" ]; then
        echo "$h"
        found=1
      fi
    done
    if [ "$found" -eq 0 ]; then
      echo "ERROR: --only $ONLY: not in HELPERS=(${HELPERS[*]})" >&2
      exit 1
    fi
  else
    for h in "${HELPERS[@]}"; do echo "$h"; done
  fi
}

# Returns "ref tag" for a helper given OPT_ALLOW_DIRTY / SHA, where
# `ref` is the fully-qualified image ref using HARBOR_HOST_REF and
# `tag` is the bare tag for logging.
build_ref() {
  local name=$1 sha=$2
  local tag="$sha"
  if [ "$OPT_ALLOW_DIRTY" -eq 1 ]; then
    tag="${sha}-dirty-$(date +%s)"
  fi
  echo "$HARBOR_HOST_REF/$HARBOR_PROJECT/$name:$tag"
}

main() {
  parse_args "$@"
  assert_not_shallow

  # Pre-flight: when not allowing dirty, every target subtree must be
  # clean. This includes the case where --only is set; only the chosen
  # helper is checked.
  if [ "$OPT_ALLOW_DIRTY" -eq 0 ]; then
    while read -r helper; do
      check_clean "$helper" \
        || { echo "ERROR: $helper subtree dirty (commit or pass --allow-dirty)" >&2
             exit 1; }
    done < <(resolve_targets)
  fi

  # Authentication is needed for the live Harbor calls (idempotency
  # check + push). Skip it for --dry-run since neither path runs.
  if [ "$OPT_DRY_RUN" -eq 0 ]; then
    harbor_login
  fi

  declare -A new_refs=()
  while read -r helper; do
    local sha ref
    sha="$(compute_sha "$helper")"
    ref="$(build_ref "$helper" "$sha")"

    if [ "$OPT_DRY_RUN" -eq 1 ]; then
      echo "[dry-run] $helper $ref"
    else
      if [ "$OPT_ALLOW_DIRTY" -eq 0 ] && harbor_has_tag "$helper" "$sha"; then
        echo "[skip] $helper:$sha already in Harbor"
      else
        echo "[build] $helper -> $ref"
        local push_ref="$HARBOR_HOST_PUSH/$HARBOR_PROJECT/$helper:${ref##*:}"
        ( cd "$REPO_ROOT" && \
          docker build --pull -t "$push_ref" "charts/lolday/helpers/$helper" )
        docker push "$push_ref"
      fi
    fi

    new_refs[$helper]="$ref"
  done < <(resolve_targets)

  # Write the lock only on a fully-clean run. --allow-dirty and
  # --dry-run never write — both cases are documented in the spec.
  if [ "$OPT_DRY_RUN" -eq 1 ] || [ "$OPT_ALLOW_DIRTY" -eq 1 ]; then
    return 0
  fi

  # Merge new_refs into the existing lock so --only NAME doesn't blow
  # away the other helper's pinned ref.
  local existing_build="" existing_job=""
  if [ -f "$LOCK_FILE" ]; then
    existing_build="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get("build_helper", ""))
' "$LOCK_FILE")"
    existing_job="$(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get("job_helper", ""))
' "$LOCK_FILE")"
  fi

  local final_build="${new_refs[build-helper]:-$existing_build}"
  local final_job="${new_refs[job-helper]:-$existing_job}"

  if [ -z "$final_build" ] || [ -z "$final_job" ]; then
    echo "ERROR: cannot write lock — missing ref for one of the helpers" >&2
    exit 1
  fi

  write_lock "$final_build" "$final_job"
  echo "[lock] $LOCK_FILE updated"
}

if [ -z "${LOLDAY_BUILD_HELPERS_SOURCED:-}" ]; then
  main "$@"
fi
```

- [ ] **Step 6: Run all tests, watch them pass**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: every `test_*.sh` reports its passes; runner exits 0.

- [ ] **Step 7: Commit**

```bash
git add scripts/build-helpers.sh tests/build-helpers/test_dry_run.sh \
        tests/build-helpers/test_only_flag.sh tests/build-helpers/test_allow_dirty.sh
git commit -m "feat(helpers): add main orchestrator with --dry-run / --only / --allow-dirty"
```

---

## Task 7: TDD `scripts/check-helpers-lock.sh` (drift guard for pre-commit + deploy)

**Files:**

- Create: `tests/build-helpers/test_check_lock.sh`
- Create: `scripts/check-helpers-lock.sh`

- [ ] **Step 1: Write the failing test**

Write `tests/build-helpers/test_check_lock.sh`:

```bash
#!/usr/bin/env bash
# scripts/check-helpers-lock.sh exits 0 when the lock matches HEAD,
# exits 1 with a drift message otherwise, and exits 0 unconditionally
# when LOLDAY_SKIP_HELPERS_LOCK_CHECK=1.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

build_sha="$(expected_sha "$repo" build-helper)"
job_sha="$(expected_sha "$repo" job-helper)"

# Seed an in-sync lock.
mkdir -p charts/lolday
cat > charts/lolday/helpers.lock <<EOF
{
  "build_helper": "harbor.lolday.svc:80/lolday/build-helper:$build_sha",
  "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:$job_sha"
}
EOF

# Override REPO_ROOT inside the script via env (the script picks it up).
LOLDAY_REPO_ROOT_OVERRIDE="$repo" bash "$CHECK_SCRIPT" \
  || fail "in-sync lock rejected"
pass "in-sync lock accepted"

# Now drift it: rewrite build_helper SHA to bogus value.
sed -i 's/'"$build_sha"'/0000000deadb/' charts/lolday/helpers.lock
if LOLDAY_REPO_ROOT_OVERRIDE="$repo" bash "$CHECK_SCRIPT" 2>/dev/null; then
  fail "drifted lock accepted"
fi
pass "drifted lock rejected"

# Skip env honoured.
LOLDAY_SKIP_HELPERS_LOCK_CHECK=1 \
  LOLDAY_REPO_ROOT_OVERRIDE="$repo" \
  bash "$CHECK_SCRIPT" \
  || fail "LOLDAY_SKIP_HELPERS_LOCK_CHECK=1 still rejected"
pass "LOLDAY_SKIP_HELPERS_LOCK_CHECK=1 short-circuits"

# Missing lock → exit 1 with a friendly message.
rm charts/lolday/helpers.lock
out="$(LOLDAY_REPO_ROOT_OVERRIDE="$repo" bash "$CHECK_SCRIPT" 2>&1)" && rc=$? || rc=$?
[ "${rc:-0}" -ne 0 ] || fail "missing lock did not exit non-zero"
echo "$out" | grep -qF "helpers.lock missing" \
  || fail "missing-lock message not surfaced"
pass "missing lock rejected with friendly message"
```

```bash
chmod +x tests/build-helpers/test_check_lock.sh
```

- [ ] **Step 2: Run, watch it fail**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: `test_check_lock.sh` fails with `bash: <CHECK_SCRIPT>: No such file or directory`.

- [ ] **Step 3: Implement `scripts/check-helpers-lock.sh`**

```bash
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
```

```bash
chmod +x scripts/check-helpers-lock.sh
```

- [ ] **Step 4: Run, watch it pass**

```bash
bash tests/build-helpers/run_all.sh
```

Expected: all six test files pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/check-helpers-lock.sh tests/build-helpers/test_check_lock.sh
git commit -m "feat(helpers): add helpers.lock drift guard for pre-commit + deploy"
```

---

## Task 8: Wire `helpers-lock-fresh` into `.pre-commit-config.yaml`

**Files:**

- Modify: `.pre-commit-config.yaml:48-77` (the `repo: local` block)

- [ ] **Step 1: Read the current local-hooks block**

```bash
grep -n -A2 "id: eslint" .pre-commit-config.yaml
```

Expected output (line numbers will vary):

```
71:      - id: eslint
72:        name: eslint
73-        language: system
```

- [ ] **Step 2: Append the new hook**

After the eslint hook block, append (preserving 6-space indentation under `hooks:`):

```yaml
- id: helpers-lock-fresh
  name: helpers.lock matches helper subtrees
  language: system
  entry: scripts/check-helpers-lock.sh
  pass_filenames: false
  files: ^charts/lolday/helpers/(build-helper|job-helper)/|^charts/lolday/helpers\.lock$
```

Verify visually with:

```bash
tail -10 .pre-commit-config.yaml
```

- [ ] **Step 3: Validate with the pre-commit framework**

```bash
pre-commit validate-config
```

Expected: no output, exit 0.

- [ ] **Step 4: Smoke-trigger the hook**

```bash
# The hook only fires on changes; with no changes staged, the hook is skipped.
# Force-run it explicitly:
pre-commit run helpers-lock-fresh --all-files
```

Expected: depending on whether `helpers.lock` exists yet, either passes (file unchanged) or fails with `helpers.lock missing — run 'bash scripts/build-helpers.sh' first`. The latter is the intended state until Task 12 produces the lock.

- [ ] **Step 5: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "feat(helpers): add helpers-lock-fresh pre-commit hook"
```

---

## Task 9: Add `helpers.lock` to `charts/lolday/.helmignore`

**Files:**

- Modify: `charts/lolday/.helmignore:18` (after the existing helpers-ignore block)

- [ ] **Step 1: Append the line**

Edit `charts/lolday/.helmignore` and add at the bottom:

```
# Helper image lockfile — release metadata, not chart payload
helpers.lock
```

Verify:

```bash
tail -3 charts/lolday/.helmignore
```

- [ ] **Step 2: Confirm `helm lint` still passes**

```bash
helm lint charts/lolday
```

Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/.helmignore
git commit -m "chore(charts): exclude helpers.lock from chart artefact"
```

---

## Task 10: Bootstrap rehearsal (live, on server30)

This task produces the initial `charts/lolday/helpers.lock` and pushes the current helper subtrees as SHA-tagged images to Harbor. It is the only non-automated step in Phase A.

**Pre-requisites:**

- Run on server30 (or any host with `kubectl` context against the lolday cluster, host docker, and `harbor.lolday.svc.cluster.local` resolvable via `/etc/hosts`).
- `harbor-push-cred` Secret already in the `lolday` namespace (created by `scripts/recover-harbor.sh` historically).
- A clean working tree on the feature branch (no uncommitted edits).

- [ ] **Step 1: Dry-run first (no Harbor calls)**

```bash
bash scripts/build-helpers.sh --dry-run
```

Expected: prints two `[dry-run] <helper> harbor.lolday.svc:80/lolday/<name>:<sha>` lines and exits 0. Capture the two SHAs for the next step.

- [ ] **Step 2: Real run**

```bash
bash scripts/build-helpers.sh
```

Expected:

- For each helper: either `[skip] <helper>:<sha> already in Harbor` (rare on a fresh-tag run) or `[build] <helper> -> <ref>` followed by docker build + push output.
- Final line: `[lock] <repo>/charts/lolday/helpers.lock updated`.

- [ ] **Step 3: Inspect the produced lock**

```bash
cat charts/lolday/helpers.lock
```

Expected: pretty-printed JSON with `build_helper` and `job_helper` keys pointing at `harbor.lolday.svc:80/lolday/<name>:<sha>` refs whose SHAs match step 1's dry-run output.

- [ ] **Step 4: Verify Harbor served both tags**

```bash
HARBOR_PASSWORD="$(grep '^export HARBOR_ADMIN_PASSWORD=' ~/.lolday-secrets.env | cut -d= -f2-)"
for h in build-helper job-helper; do
  sha="$(jq -r ".${h//-/_}" charts/lolday/helpers.lock | awk -F: '{print $NF}')"
  curl -fsS -u "admin:$HARBOR_PASSWORD" \
    "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/$h/artifacts?with_tag=true&q=tags=$sha" \
    | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(f"{len(d)} artefact(s) for the SHA")
'
done
```

Expected: each helper reports `1 artefact(s) for the SHA`.

- [ ] **Step 5: Commit the lock**

```bash
git add charts/lolday/helpers.lock
git commit -m "feat(helpers): pin initial helper image refs to subtree SHAs"
```

The pre-commit `helpers-lock-fresh` hook will fire and pass (lock matches HEAD subtrees by construction).

---

## Task 11: TDD `validate_helper_images` model_validator

**Files:**

- Modify: `backend/tests/test_config_validation.py` (append four cases)
- Modify: `backend/app/config.py:18,30,67-84` (defaults to "" + new validator)

- [ ] **Step 1: Append the failing test cases**

Append to `backend/tests/test_config_validation.py`:

```python
def test_settings_rejects_empty_build_image_helper_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "x" * 64)
    monkeypatch.setenv("BUILD_IMAGE_HELPER", "")
    monkeypatch.setenv("JOB_HELPER_IMAGE", "harbor.lolday.svc:80/lolday/job-helper:abc")

    from app.config import Settings

    with pytest.raises(ValidationError, match="BUILD_IMAGE_HELPER must be set"):
        Settings()


def test_settings_rejects_empty_job_helper_image_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "x" * 64)
    monkeypatch.setenv("BUILD_IMAGE_HELPER", "harbor.lolday.svc:80/lolday/build-helper:abc")
    monkeypatch.setenv("JOB_HELPER_IMAGE", "")

    from app.config import Settings

    with pytest.raises(ValidationError, match="JOB_HELPER_IMAGE must be set"):
        Settings()


def test_settings_accepts_filled_helper_images_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_APP_AUD", "x" * 64)
    monkeypatch.setenv("BUILD_IMAGE_HELPER", "harbor.lolday.svc:80/lolday/build-helper:abc")
    monkeypatch.setenv("JOB_HELPER_IMAGE", "harbor.lolday.svc:80/lolday/job-helper:def")

    from app.config import Settings

    s = Settings()
    assert s.BUILD_IMAGE_HELPER.endswith(":abc")
    assert s.JOB_HELPER_IMAGE.endswith(":def")


def test_settings_accepts_empty_helper_images_outside_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BUILD_IMAGE_HELPER", "")
    monkeypatch.setenv("JOB_HELPER_IMAGE", "")

    from app.config import Settings

    s = Settings()
    assert s.BUILD_IMAGE_HELPER == ""
    assert s.JOB_HELPER_IMAGE == ""
```

- [ ] **Step 2: Run, watch them fail**

```bash
cd backend && uv run pytest tests/test_config_validation.py -v
```

Expected: the four new tests fail because the current defaults are non-empty `:v3` / `:v4` strings (so empty-string env vars get coerced to those defaults, and the validator does not exist yet).

- [ ] **Step 3: Edit `backend/app/config.py`**

Change line 18:

```python
BUILD_IMAGE_HELPER: str = "harbor.harbor.svc:80/lolday/build-helper:v3"
```

to:

```python
BUILD_IMAGE_HELPER: str = ""
```

Change line 30:

```python
JOB_HELPER_IMAGE: str = "harbor.harbor.svc:80/lolday/job-helper:v4"
```

to:

```python
JOB_HELPER_IMAGE: str = ""
```

After the existing `validate_sso_config` method (currently at lines 67-84), add:

```python
    @model_validator(mode="after")
    def validate_helper_images(self) -> "Settings":
        """Fail-fast on production misconfiguration. Helper image refs are
        produced by scripts/build-helpers.sh into charts/lolday/helpers.lock
        and injected by scripts/deploy.sh — never hardcoded as defaults."""
        if self.ENVIRONMENT != "production":
            return self
        missing = [
            name for name in ("BUILD_IMAGE_HELPER", "JOB_HELPER_IMAGE")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(
                f"{', '.join(missing)} must be set in production. "
                "Produce the values via scripts/build-helpers.sh and inject "
                "them via scripts/deploy.sh."
            )
        return self
```

- [ ] **Step 4: Run, watch them pass**

```bash
cd backend && uv run pytest tests/test_config_validation.py -v
```

Expected: all eight tests pass (four pre-existing SSO cases + four new helper cases).

- [ ] **Step 5: Run the full backend test suite**

```bash
cd backend && uv run pytest
```

Expected: full suite passes — the empty defaults rely on `ENVIRONMENT=test` from `conftest.py` to short-circuit the validator.

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/tests/test_config_validation.py
git commit -m "feat(backend): fail-fast on missing helper image refs in production"
```

---

## Task 12: Delete the three dead helper-image keys from `values.yaml`

**Files:**

- Modify: `charts/lolday/values.yaml:56,65,254`

- [ ] **Step 1: Remove `backend.env.BUILD_IMAGE_HELPER`**

Open `charts/lolday/values.yaml` and delete the line:

```yaml
BUILD_IMAGE_HELPER: "harbor.lolday.svc:80/lolday/build-helper:v3"
```

- [ ] **Step 2: Remove `backend.env.JOB_HELPER_IMAGE`**

Delete the line:

```yaml
JOB_HELPER_IMAGE: "harbor.lolday.svc:80/lolday/job-helper:v4"
```

- [ ] **Step 3: Remove `jobs.helperImage`**

Delete the line:

```yaml
helperImage: harbor.lolday.svc:80/lolday/job-helper:v4
```

(After the deletion, the `jobs:` block opens directly with `activeDeadlineSeconds:` — see existing context lines 252-263.)

- [ ] **Step 4: Verify with `helm template`**

The chart must still render without these keys (the missing env entries become a backend pod that fails fast on boot, which is by design):

```bash
helm template lolday charts/lolday \
  --namespace lolday \
  --set monitoring.postgresExporter.password=dummy \
  --set monitoring.grafana.adminPassword=dummy \
  --set mlflow.db.password=dummy \
  --set backend.harborAdminPassword=dummy \
  --set backend.fernetKey=dummy \
  --set cloudflare.enabled=false \
  --set postgresql.auth.password=dummy \
  > /tmp/render.yaml
```

Expected: exit 0. The rendered backend Deployment has a `BUILD_NAMESPACE` and `JOB_NAMESPACE` env entry (from the templated block), no `BUILD_IMAGE_HELPER` / `JOB_HELPER_IMAGE` (those are absent until deploy.sh injects).

```bash
grep -E '(BUILD_IMAGE_HELPER|JOB_HELPER_IMAGE)' /tmp/render.yaml || echo "absent (expected)"
```

Expected: prints `absent (expected)`.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/values.yaml
git commit -m "refactor(charts): drop dead helper-image keys from values.yaml"
```

---

## Task 13: Add lock-read + drift-guard + injection to `scripts/deploy.sh`

**Files:**

- Modify: `scripts/deploy.sh:42-58` (insert before pre-flight cluster check) and `scripts/deploy.sh:177-190` (helm upgrade --set list)

- [ ] **Step 1: Insert the lock-read + drift-guard block**

After the existing `BACKEND_IMAGE` / `FRONTEND_IMAGE` defaults (around line 41) and before the `# Pre-flight` comment (around line 43), insert:

```bash
# ---------------------------------------------------------------------------
# Helper images — read SHA-pinned refs from the lockfile and drift-guard
# them against the current HEAD subtrees. The lock is produced by
# scripts/build-helpers.sh; see docs/runbooks/release-helpers.md.
# ---------------------------------------------------------------------------
HELPERS_LOCK="$CHART_DIR/helpers.lock"
if [ ! -f "$HELPERS_LOCK" ]; then
  echo "ERROR: $HELPERS_LOCK missing — run 'bash scripts/build-helpers.sh' first" >&2
  exit 1
fi

if ! "$SCRIPT_DIR/check-helpers-lock.sh"; then
  exit 1
fi

BUILD_IMAGE_HELPER=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["build_helper"])' "$HELPERS_LOCK")
JOB_HELPER_IMAGE=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["job_helper"])'  "$HELPERS_LOCK")
```

- [ ] **Step 2: Add the two `--set` lines to `helm upgrade`**

In the `helm upgrade --install lolday` invocation (around lines 177-190), add two lines just before `--wait`:

```bash
  --set backend.env.BUILD_IMAGE_HELPER="$BUILD_IMAGE_HELPER" \
  --set backend.env.JOB_HELPER_IMAGE="$JOB_HELPER_IMAGE" \
  --wait --timeout 20m
```

- [ ] **Step 3: Sanity-syntax check**

```bash
bash -n scripts/deploy.sh
```

Expected: exit 0.

- [ ] **Step 4: Smoke-test the drift guard locally**

Without modifying the actual repo state, simulate drift:

```bash
# Save and corrupt the lock.
cp charts/lolday/helpers.lock /tmp/lock.bak
sed -i 's/build-helper:[0-9a-f]\{12\}/build-helper:0000000bad00/' charts/lolday/helpers.lock

# Run only the lock-read block by extracting it. Easier: just call check-helpers-lock.sh directly.
bash scripts/check-helpers-lock.sh && echo "UNEXPECTED PASS" || echo "Drift caught (expected)"

# Restore.
mv /tmp/lock.bak charts/lolday/helpers.lock
```

Expected: prints `Drift caught (expected)`.

- [ ] **Step 5: Commit**

```bash
git add scripts/deploy.sh
git commit -m "feat(deploy): inject helper image refs from helpers.lock"
```

---

## Task 14: Update `scripts/recover-harbor.sh`

**Files:**

- Modify: `scripts/recover-harbor.sh:286-296` (the build_push / skip_if_missing block)

- [ ] **Step 1: Remove the helper-image lines**

Delete:

```bash
build_push      "$REPO/charts/lolday/helpers/build-helper"        "lolday/build-helper:v2"
skip_if_missing "$REPO/charts/lolday/helpers/job-helper"          "lolday/job-helper:v2"
```

(Keep `lolday-backend`, `mlflow-server`, and `lolday-frontend` lines unchanged.)

- [ ] **Step 2: Tail-call `build-helpers.sh` if the lock exists**

After the closing of the `=== rebuilding + pushing core images ===` block (and before the trailing summary), insert:

```bash
# ---------------------------------------------------- 6. helper images
# Helper image release is owned by scripts/build-helpers.sh (content-
# addressable subtree SHAs + helpers.lock). If the lock is in place,
# delegate; otherwise, instruct the operator to run it manually.
LOCK="$REPO/charts/lolday/helpers.lock"
if [ -f "$LOCK" ]; then
  echo
  echo "=== helper images: delegating to scripts/build-helpers.sh ==="
  bash "$REPO/scripts/build-helpers.sh"
else
  echo
  echo "WARN: $LOCK not found — helper images not pushed." >&2
  echo "      Next step: bash $REPO/scripts/build-helpers.sh" >&2
fi
```

- [ ] **Step 3: Sanity-syntax check**

```bash
bash -n scripts/recover-harbor.sh
```

Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/recover-harbor.sh
git commit -m "refactor(scripts): delegate helper image build to build-helpers.sh"
```

---

## Task 15: Write `docs/runbooks/release-helpers.md`

**Files:**

- Create: `docs/runbooks/release-helpers.md`

- [ ] **Step 1: Write the runbook**

````markdown
# Release runbook: helper images

> Live runbook. Sister doc to `docs/runbooks/deploy.md`.
>
> SSH safety hard rule applies — see root `CLAUDE.md`.

This runbook covers the release flow for the two platform-side helper images:

- `harbor.lolday.svc:80/lolday/build-helper:<sha>` — build-pipeline init container.
- `harbor.lolday.svc:80/lolday/job-helper:<sha>` — job-pod init container, model-fetcher, and event-tailer sidecar.

`mlflow-server` and `pytorch-cu12-base` are out of scope; their tags carry external semantic meaning and stay manually pinned.

Spec: `docs/superpowers/specs/2026-04-29-helper-image-versioning-design.md`.

## Pre-requisites

- Host docker (the operator's machine, typically server30) with network reach to `harbor.lolday.svc.cluster.local:80`.
- `kubectl` context pointing at the lolday cluster.
- `harbor-push-cred` Secret already in the `lolday` namespace. Create it via `bash scripts/recover-harbor.sh` if missing.
- A clean working tree on the feature branch — the build script refuses dirty subtrees.

## Standard flow

1. Edit the helper source — anything under `charts/lolday/helpers/<name>/` (Dockerfile, `pyproject.toml`, `uv.lock`, source files, tests). The 12-char subtree SHA captures every file that git tracks.
2. Run any per-helper unit tests:
   - `cd backend && uv run pytest charts/lolday/helpers/build-helper/test_maldet_validator.py`
   - `cd charts/lolday/helpers/job-helper && uv run pytest`
3. Commit the source change. The build script reads `HEAD:<path>` so the change must be committed before the SHA reflects it.
4. Run the build script:

   ```bash
   bash scripts/build-helpers.sh
   ```
````

Output:

- `[skip] <name>:<sha> already in Harbor` — Harbor already serves this SHA, no rebuild.
- `[build] <name> -> <ref>` followed by docker build + push output.
- `[lock] charts/lolday/helpers.lock updated` at the end.

5. Inspect the lock diff and commit:

   ```bash
   git diff charts/lolday/helpers.lock
   git commit charts/lolday/helpers.lock -m "chore(helpers): rebuild <name> at <sha>"
   ```

6. Deploy:

   ```bash
   bash scripts/deploy.sh
   ```

   The deploy script reads the lock, drift-guards it against HEAD, and injects the two image refs via Helm `--set`. A drift exits 1 with a diff message — re-run `bash scripts/build-helpers.sh` and commit the lock to fix.

## Variants

| Flag                                                | When to use                                                                                                                                                                                                                                                                                                                                                           |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bash scripts/build-helpers.sh --dry-run`           | Preview SHAs and image refs without contacting docker / Harbor / kubectl. Useful for sanity-checking the next tag in a PR description.                                                                                                                                                                                                                                |
| `bash scripts/build-helpers.sh --only build-helper` | Iterate on one helper without rebuilding the other. Updates only that key in the lock.                                                                                                                                                                                                                                                                                |
| `bash scripts/build-helpers.sh --allow-dirty`       | Dev-loop iteration on uncommitted changes. Stamps the tag with `-dirty-<unix-ts>`, builds and pushes, but leaves the lock untouched. **Never** use for a production rollout: an unreproducible tag is not what `helpers.lock` should pin. To deploy a `-dirty` image manually, pass it through `--set backend.env.BUILD_IMAGE_HELPER=...` to `helm upgrade` directly. |

## Rollback

`git revert` the lock commit and redeploy:

```bash
git revert <commit-sha-of-lock-bump>
bash scripts/deploy.sh
```

Older SHA tags persist in Harbor (Harbor does not auto-prune), so rollback is a redeploy of the previous lock — no rebuild needed. To wipe a tag from Harbor manually, use the Harbor UI or `scripts/harbor-inventory.sh`.

## Bootstrap (first-time install)

The lock is committed to git, but its tagged images do not exist in a fresh Harbor. Bootstrap order on a clean cluster:

1. `bash scripts/install-tools.sh`
2. `sudo bash scripts/setup-k3s.sh`
3. `bash scripts/deploy.sh` — first round, brings up Harbor and the platform; the backend pod will not yet have helper images, expect CrashLoopBackOff.
4. `bash scripts/recover-harbor.sh` — creates the Harbor `lolday` project, the `robot$build-pusher` account, and the `harbor-push-cred` Secret.
5. `bash scripts/build-helpers.sh` — pushes the helper images for the SHAs already pinned in the committed lock.
6. `bash scripts/deploy.sh` — second round; the backend pod now boots clean.

## CI integration sketch

Not implemented in this phase. The build script is pure-functional:

- Input: working tree git state.
- Output: Harbor pushes (idempotent against existing SHAs) + `helpers.lock` rewrite.

A future GitHub Actions / CI workflow can wrap it as:

```yaml
- run: bash scripts/build-helpers.sh
- run: |
    if ! git diff --exit-code charts/lolday/helpers.lock; then
      gh pr edit "$PR_NUMBER" --body "$(cat <<EOF
    Helper images rebuilt; lock updated. Reviewer: confirm and merge.
    EOF
    )"
      git config user.email "ci@lolday"
      git config user.name "lolday-ci"
      git add charts/lolday/helpers.lock
      git commit -m "chore(helpers): auto-rebuild helper image refs"
      git push
    fi
```

## Failure modes

| Symptom                                           | Cause                                                     | Fix                                                                                                                                               |
| ------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `harbor-push-cred not found`                      | First-time install or after `helm uninstall`              | `bash scripts/recover-harbor.sh`                                                                                                                  |
| `shallow clone detected`                          | Cloned with `--depth=N`                                   | `git fetch --unshallow`                                                                                                                           |
| `<helper> subtree dirty`                          | Uncommitted edit or untracked file under the helper       | `git status charts/lolday/helpers/<helper>` and either commit, stash, or pass `--allow-dirty`                                                     |
| `helpers.lock drift detected` from `deploy.sh`    | Helper subtree changed but lock not regenerated           | `bash scripts/build-helpers.sh` and commit the new lock                                                                                           |
| Pod stuck in `ImagePullBackOff` after deploy      | Harbor lost the tag (unusual — tag was deleted manually?) | `bash scripts/build-helpers.sh` to re-push; the same SHA tag is regenerated                                                                       |
| Pre-commit hook trips with `helpers.lock missing` | Fresh clone before the bootstrap rehearsal                | Run `bash scripts/build-helpers.sh` then commit the lock; or set `LOLDAY_SKIP_HELPERS_LOCK_CHECK=1` for the single commit if the build cannot run |

````

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/release-helpers.md
git commit -m "docs(helpers): add release runbook"
````

---

## Task 16: Update `README.md` setup section

**Files:**

- Modify: `README.md:9-32` (Setup block)

- [ ] **Step 1: Re-read the current Setup block**

```bash
sed -n '7,40p' README.md
```

- [ ] **Step 2: Insert step 5 (build helpers) before the existing step 4 (deploy)**

Restructure the `## Setup` block so that the steps read:

````markdown
## Setup

```bash
# 1. Install CLI tools (no sudo)
bash scripts/install-tools.sh

# 2. Install K3s (requires sudo — run with a sudo-capable account)
sudo bash scripts/setup-k3s.sh

# 3. Install GPU Operator (no sudo)
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
helm install gpu-operator nvidia/gpu-operator \
  -n gpu-operator --create-namespace \
  --set driver.enabled=false \
  --set toolkit.enabled=true \
  --set devicePlugin.enabled=true \
  --set dcgmExporter.enabled=true \
  --wait --timeout 5m

# 4. Deploy the platform — first round (no sudo)
#    Brings up Harbor + monitoring; backend will CrashLoopBackOff until
#    helper images are pushed in step 6.
bash scripts/deploy.sh

# 5. Bootstrap Harbor projects + robot account
bash scripts/recover-harbor.sh

# 6. Build and push helper images (writes/refreshes charts/lolday/helpers.lock)
bash scripts/build-helpers.sh

# 7. Deploy again — backend now starts clean
bash scripts/deploy.sh
```
````

````

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document helper-image bootstrap order in README"
````

---

## Task 17: Update root `CLAUDE.md` Quickstart commands

**Files:**

- Modify: `CLAUDE.md:60-69` (Quickstart commands code block)

- [ ] **Step 1: Append the build-helpers line**

After the `bash scripts/deploy.sh` line, add:

```bash
bash scripts/build-helpers.sh         # build + push helper images, refresh helpers.lock
```

The block should now read:

```bash
bash scripts/install-tools.sh           # CLI tools, no sudo → ~/.local/bin/
sudo bash scripts/setup-k3s.sh          # K3s install — give to sudo-capable account
bash scripts/deploy.sh                  # platform deploy, no sudo
bash scripts/build-helpers.sh           # build + push helper images, refresh helpers.lock
cd backend && uv run pytest             # backend tests
cd frontend && pnpm test                # frontend unit (vitest)
cd frontend && pnpm playwright test     # frontend E2E
helm lint charts/lolday                 # helm sanity
pre-commit run --all-files              # lint+format whole repo (also auto-runs on git commit)
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): add build-helpers.sh to quickstart"
```

---

## Task 18: Update `docs/runbooks/deploy.md`

**Files:**

- Modify: `docs/runbooks/deploy.md:116-130` (§5 deploy block)

- [ ] **Step 1: Add a sub-step before the existing `bash scripts/deploy.sh`**

Find:

````markdown
## 5. Deploy the platform (no sudo)

```bash
bash scripts/deploy.sh
```
````

Internally: `helm dependency update charts/lolday` then `helm upgrade --install lolday charts/lolday -n lolday`. The script reads `.lolday-secrets.env` for operator-managed secrets.

````

Replace with:

```markdown
## 5. Deploy the platform (no sudo)

The deploy script reads `charts/lolday/helpers.lock` and injects the two helper image refs via Helm `--set`. **The lock must exist and match the current HEAD** — see `docs/runbooks/release-helpers.md` for the bootstrap order on a fresh install. A missing or drifted lock exits 1 with a remediation message.

```bash
bash scripts/deploy.sh
````

Internally: `helm dependency update charts/lolday` then `helm upgrade --install lolday charts/lolday -n lolday`. The script reads `.lolday-secrets.env` for operator-managed secrets and `charts/lolday/helpers.lock` for helper image refs.

````

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/deploy.md
git commit -m "docs(deploy): cross-reference helpers.lock + release runbook"
````

---

## Task 19: Mark `docs/architecture.md` §9 #4 + #8 resolved

**Files:**

- Modify: `docs/architecture.md:286-300` (§9 entries 4 and 8)

- [ ] **Step 1: Find and rewrite entry #4**

Replace:

```markdown
4. **Helper images built by hand.** No automated build/push of `build-helper:vN` / `job-helper:vN`.
```

with:

```markdown
4. ~~**Helper images built by hand.**~~ — resolved 2026-04-29 in `feat/helper-image-versioning`: `scripts/build-helpers.sh` automates content-addressable build + push (subtree SHA tag), idempotent against Harbor. Spec: `docs/superpowers/specs/2026-04-29-helper-image-versioning-design.md`. Runbook: `docs/runbooks/release-helpers.md`.
```

- [ ] **Step 2: Find and rewrite entry #8**

Replace:

```markdown
8. **Helper image versions hardcoded** — `BUILD_IMAGE_HELPER=v3`, `JOB_HELPER_IMAGE=v4` in `config.py`. No versioning strategy.
```

with:

```markdown
8. ~~**Helper image versions hardcoded.**~~ — resolved 2026-04-29: tags are now 12-char subtree SHAs pinned in `charts/lolday/helpers.lock` and injected by `scripts/deploy.sh`; the `BUILD_IMAGE_HELPER` / `JOB_HELPER_IMAGE` defaults in `backend/app/config.py` are empty strings, with a `validate_helper_images` model_validator that fails boot in production when either is unset. mlflow-server and pytorch-cu12-base remain on manual semantic tags by design (their tags carry external meaning).
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "docs(arch): mark §9 #4 and #8 (helper images) resolved"
```

---

## Task 20: Update `.claude/rules/charts-and-helm.md`

**Files:**

- Modify: `.claude/rules/charts-and-helm.md:42-55` (Helper images section)

- [ ] **Step 1: Rewrite the Helper images block**

Replace:

```markdown
## Helper images (`charts/lolday/helpers/`)

Each helper has its own Dockerfile, built and pushed manually by the operator.

- `build-helper/` — Python tool. Includes `maldet_validator.py` which asserts a built detector matches the maldet spec. Has its own `pyproject.toml` + `uv.lock` + `test_maldet_validator.py`.
- `job-helper/` — Python module + tests + `uv.lock`. This is the entrypoint inside vcjob containers.
- `mlflow-server/` — Dockerfile only; produces a custom mlflow image.
- `pytorch-cu12-base/` — Dockerfile only; GPU base image.

Image tags are hardcoded in `backend/app/config.py`:

- `BUILD_IMAGE_HELPER` defaults to `harbor.harbor.svc:80/lolday/build-helper:v3`
- `JOB_HELPER_IMAGE` defaults to `harbor.lolday.svc:80/lolday/job-helper:v4` (note: Harbor URL inconsistency — see `docs/architecture.md` §9)
```

with:

```markdown
## Helper images (`charts/lolday/helpers/`)

Four helpers, two release flows.

### Content-addressable (managed by `scripts/build-helpers.sh`)

- `build-helper/` — Python tool. Includes `maldet_validator.py` which asserts a built detector matches the maldet spec. Has its own `pyproject.toml` + `uv.lock` + `test_maldet_validator.py`.
- `job-helper/` — Python module + tests + `uv.lock`. The vcjob init / sidecar / model-fetcher container.

Tags are 12-char subtree SHAs derived from `git rev-parse HEAD:charts/lolday/helpers/<name>`. They are pinned in `charts/lolday/helpers.lock` (JSON, git-tracked) and injected at deploy time via `scripts/deploy.sh --set backend.env.BUILD_IMAGE_HELPER=... --set backend.env.JOB_HELPER_IMAGE=...`.

`backend/app/config.py` has empty defaults for both env vars and a `validate_helper_images` model_validator that fails boot in production when either is unset. The pre-commit hook `helpers-lock-fresh` blocks commits that leave the lock out of sync with the helper subtrees.

Operator flow → `docs/runbooks/release-helpers.md`. Spec → `docs/superpowers/specs/2026-04-29-helper-image-versioning-design.md`.

### Manually pinned (semantic tags)

- `mlflow-server/` — Dockerfile only; produces the custom MLflow image. Tag = upstream MLflow version, e.g. `:v2.20.3`.
- `pytorch-cu12-base/` — Dockerfile only; GPU base image. Tag = `<torch>-<cuda>` set, e.g. `:2.7.0-cu126`.

These do not flow through `helpers.lock`; their tags carry external semantic meaning that subtree SHA strips. Bumping them is a manual edit to the relevant `values.yaml` line + a `docker build` + `docker push` from the operator's host.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/rules/charts-and-helm.md
git commit -m "docs(rules): update helper-images section for lock-based release"
```

---

## Task 21: Update `.claude/rules/scripts-and-ops.md`

**Files:**

- Modify: `.claude/rules/scripts-and-ops.md:7-17` (script categories)

- [ ] **Step 1: Add `build-helpers.sh` to the install/deploy category**

Replace:

```markdown
- **Install / deploy** — `install-tools.sh` (CLI tools to `~/.local/bin/`, no sudo), `setup-k3s.sh` (sudo-required, hand to operator), `deploy.sh` (Helm dep update + upgrade --install), `teardown.sh`.
```

with:

```markdown
- **Install / deploy** — `install-tools.sh` (CLI tools to `~/.local/bin/`, no sudo), `setup-k3s.sh` (sudo-required, hand to operator), `deploy.sh` (Helm dep update + upgrade --install), `build-helpers.sh` (helper-image release: subtree SHA tag, idempotent push, writes `charts/lolday/helpers.lock`; runbook `docs/runbooks/release-helpers.md`), `check-helpers-lock.sh` (drift guard used by pre-commit + deploy), `teardown.sh`.
```

- [ ] **Step 2: Add a "Helper image release" sub-section after "Engineering hygiene 紀律"**

Append at the end of the file (or after the Engineering hygiene section):

```markdown
## Helper image release 紀律

`scripts/build-helpers.sh` is the only sanctioned way to push `build-helper` and `job-helper` images. Tags are content-addressable (12-char subtree SHA from `git rev-parse HEAD:<path>`) and pinned in `charts/lolday/helpers.lock`.

### Forbidden

- Hardcoding helper image refs in `backend/app/config.py`, `charts/lolday/values.yaml`, or anywhere else. The lock is the only source of truth.
- Pushing a `-dirty-<ts>` tag from `--allow-dirty` to a production deploy. The lock never records dirty tags; using one in `helm upgrade --set ...` is a deliberate operator override and must be justified in the deploy log.

### Rules

- The dirty-tree refusal in `build-helpers.sh` is intentional. To iterate on uncommitted changes, pass `--allow-dirty` knowingly — the runbook covers the rule.
- The pre-commit `helpers-lock-fresh` hook fails on drift. Override only with `LOLDAY_SKIP_HELPERS_LOCK_CHECK=1` and only when the build itself cannot run (no docker, no kubectl); otherwise fix the root cause by re-running `build-helpers.sh`.
- `scripts/recover-harbor.sh` no longer rebuilds helper images directly. It tail-calls `build-helpers.sh` if the lock exists; otherwise it points the operator to run it manually.
- Adding a new helper means: edit the `HELPERS=(...)` array in `build-helpers.sh`, edit the JSON keys in `helpers.lock` and the script's `write_lock` body, add the corresponding `--set` line in `deploy.sh`, and document in `docs/runbooks/release-helpers.md`.
```

- [ ] **Step 3: Commit**

```bash
git add .claude/rules/scripts-and-ops.md
git commit -m "docs(rules): document helper-image release discipline"
```

---

## Task 22: Final end-to-end rehearsal on server30

This task is a manual smoke test. It verifies the complete migration on a real cluster.

**Pre-requisites:**

- All Tasks 1-21 merged or staged.
- The feature branch in a clean state (`git status` clean).
- SSH session to server30 open in a separate terminal (per the SSH safety hard rule).

- [ ] **Step 1: Pre-flight — confirm baseline**

```bash
git status
helm lint charts/lolday
cd backend && uv run pytest && cd ..
bash tests/build-helpers/run_all.sh
pre-commit run --all-files
```

Expected: all green.

- [ ] **Step 2: Apply a trivial helper change to exercise the full loop**

```bash
echo "# rehearsal $(date +%s)" >> charts/lolday/helpers/build-helper/maldet_validator.py
git add charts/lolday/helpers/build-helper/maldet_validator.py
git commit -m "test(helpers): rehearsal-only edit to build-helper"
```

- [ ] **Step 3: Try to commit again to confirm drift guard fires**

(Sanity check — pre-commit should already have run on step 2. Confirm the lock is now drifted by attempting a no-op commit.)

```bash
git commit --allow-empty -m "drift check"
```

Expected: pre-commit fails with `helpers.lock drift detected: build-helper: lock=... HEAD=...:<new-sha>`.

- [ ] **Step 4: Rebuild helpers, commit lock**

```bash
bash scripts/build-helpers.sh
git diff charts/lolday/helpers.lock   # confirm the build-helper SHA flipped, job-helper unchanged
git add charts/lolday/helpers.lock
git commit -m "test(helpers): rehearsal lock bump"
```

Expected: `[build] build-helper -> harbor.lolday.svc:80/lolday/build-helper:<new-sha>` and `[skip] job-helper:<unchanged-sha> already in Harbor`.

- [ ] **Step 5: Deploy**

```bash
bash scripts/deploy.sh
```

Expected: lock-read passes, drift guard passes, helm upgrade succeeds, backend pod rolls without CrashLoopBackOff.

- [ ] **Step 6: Confirm the running backend uses the new tag**

```bash
kubectl -n lolday get deploy backend -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="BUILD_IMAGE_HELPER")].value}'
```

Expected: ends with `:<new-sha>` from step 4.

- [ ] **Step 7: Roll back the rehearsal commits (optional)**

```bash
git revert HEAD~1..HEAD --no-edit   # revert lock bump + source edit
bash scripts/build-helpers.sh        # idempotent re-push of original SHAs
bash scripts/deploy.sh               # back to baseline
```

- [ ] **Step 8: Final acceptance check**

Walk through every item in `docs/superpowers/specs/2026-04-29-helper-image-versioning-design.md` § Acceptance Criteria. Each must be verifiable with the artefacts now in the repo:

1. ✅ `scripts/build-helpers.sh` runs end-to-end on a clean tree.
2. ✅ `charts/lolday/helpers.lock` committed.
3. ✅ `backend/app/config.py` validators pass; `cd backend && uv run pytest` green.
4. ✅ `charts/lolday/values.yaml` deletions; `helm lint charts/lolday` green.
5. ✅ `scripts/deploy.sh` reads the lock; missing/drift case exits 1.
6. ✅ `scripts/recover-harbor.sh` delegates.
7. ✅ Pre-commit hook fires on drift.
8. ✅ `docs/runbooks/release-helpers.md`, `README.md`, `CLAUDE.md`, `docs/runbooks/deploy.md`, `docs/architecture.md` §9, `.claude/rules/charts-and-helm.md`, `.claude/rules/scripts-and-ops.md` updated.
9. ✅ Round-trip rehearsal succeeded (steps 2–6 above).

If any item fails, raise it in the PR description and resolve before merge.

- [ ] **Step 9: Squash to the two migration-plan commits**

The migration plan calls for two reviewable commits. During development the branch carries one commit per task; squash them at PR-time:

```bash
# On the feature branch, count commits since main:
git log --oneline main..HEAD | wc -l

# Interactive rebase to squash into two: commit 1 (introduce flow), commit 2 (switch deploy).
# Suggested boundary: commit 2 starts at Task 11 (config.py).
git rebase -i main
# Mark Tasks 1-10 as 's' under the first commit; Tasks 11-21 under the second.
```

Final commit messages:

- `feat(helpers): introduce content-addressable build flow`
- `feat(helpers): switch deploy to lock-pinned helper images`

Open the PR. Done.

---

## Self-Review

**Spec coverage check:**

| Spec section                                          | Plan task                                    |
| ----------------------------------------------------- | -------------------------------------------- |
| Versioning strategy (12-char subtree SHA)             | Task 2 (`compute_sha`)                       |
| `build-helpers.sh` contract — flag parsing            | Task 6 (`parse_args`)                        |
| `build-helpers.sh` contract — auth                    | Task 5 (`harbor_login`)                      |
| `build-helpers.sh` contract — idempotency             | Task 5 (`harbor_has_tag`)                    |
| `build-helpers.sh` contract — build/push              | Task 5 (`docker_build_push`)                 |
| `build-helpers.sh` contract — dirty/shallow           | Task 3 (`check_clean`, `assert_not_shallow`) |
| `build-helpers.sh` contract — atomic lock write       | Task 4 (`write_lock`)                        |
| `build-helpers.sh` contract — `--allow-dirty` no-lock | Task 6 + test_allow_dirty.sh                 |
| `build-helpers.sh` contract — `--only NAME`           | Task 6 + test_only_flag.sh                   |
| `helpers.lock` JSON format                            | Task 4 (write_lock) + Task 9 (.helmignore)   |
| `deploy.sh` lock-read + drift guard + injection       | Task 13                                      |
| `config.py` empty defaults + validator                | Task 11                                      |
| `values.yaml` deletions                               | Task 12                                      |
| `recover-harbor.sh` cleanup + delegation              | Task 14                                      |
| Pre-commit hook                                       | Task 7 (script) + Task 8 (hook entry)        |
| First-time bootstrap order                            | Task 16 (README) + Task 18 (deploy.md)       |
| Documentation updates                                 | Tasks 15, 16, 17, 18, 19, 20, 21             |
| Migration plan two-commit split                       | Task 22 step 9                               |
| Acceptance criteria                                   | Task 22 step 8                               |

No gaps.

**Placeholder scan:** searched for "TBD", "TODO", "implement later" — none present. Every code-change step has full code; every command has expected output.

**Type / name consistency:** verified `BUILD_IMAGE_HELPER` / `JOB_HELPER_IMAGE` (constant case), `build_helper` / `job_helper` (lock keys, snake), `build-helper` / `job-helper` (directory + helper names, kebab) used consistently. Mapping `key.replace("_", "-")` appears in both `scripts/check-helpers-lock.sh` (Task 7) and the deploy.sh drift guard (Task 13).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-29-helper-image-versioning.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?
