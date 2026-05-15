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

# REPO_ROOT defaults to the repo containing this script. Tests override
# via LOLDAY_REPO_ROOT_OVERRIDE so they can point at a fixture repo
# under /tmp; scripts/check-helpers-lock.sh uses the same convention.
REPO_ROOT="${LOLDAY_REPO_ROOT_OVERRIDE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCK_FILE="$REPO_ROOT/charts/lolday/helpers.lock"
HARBOR_HOST_PUSH=harbor.lolday.svc.cluster.local:80   # docker push target (host /etc/hosts)
HARBOR_HOST_REF=harbor.lolday.svc:80                  # value embedded in image refs
HARBOR_PROJECT=lolday
HELPERS=(build-helper job-helper)

# ----- pure helpers ----------------------------------------------------

# compute_sha NAME — print the first 12 hex chars of the subtree's tree
# SHA at HEAD. Aborts if the path is missing in HEAD. Operates against
# the git repo at REPO_ROOT (set by LOLDAY_REPO_ROOT_OVERRIDE in tests).
compute_sha() {
  local name=$1
  git -C "$REPO_ROOT" rev-parse --short=12 "HEAD:charts/lolday/helpers/$name"
}

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
    # shellcheck disable=SC2086 # intentional word-split: one path per line
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

# write_lock BUILD_HELPER_REF JOB_HELPER_REF — atomically writes
# helpers.lock as pretty-printed JSON with snake_case keys. Uses python3
# (already a hard dep of deploy.sh) so the output is always valid JSON.
# As of H-21-img the refs include the @sha256:<digest> suffix; the
# function itself doesn't care about the format, but downstream
# check-helpers-lock.sh asserts it.
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

# _harbor_creds_ns — print the namespace where the harbor-push-cred Secret
# lives, or empty + return 1 if absent. recover-harbor.sh creates the
# Secret in BOTH `lolday` AND `lolday-jobs` (see scripts/recover-harbor.sh
# `for NS in lolday lolday-jobs`); historically the lolday copy has been
# pruned between runs, so the script falls through both. Cached in
# $HARBOR_CRED_NS for subsequent calls within the same invocation.
_harbor_creds_ns() {
  if [ -n "${HARBOR_CRED_NS:-}" ]; then
    echo "$HARBOR_CRED_NS"
    return 0
  fi
  local ns
  for ns in lolday lolday-jobs; do
    if kubectl -n "$ns" get secret harbor-push-cred >/dev/null 2>&1; then
      HARBOR_CRED_NS="$ns"
      echo "$ns"
      return 0
    fi
  done
  return 1
}

# harbor_login — pull the robot$build-pusher credentials out of the
# K8s harbor-push-cred Secret, decode the dockerconfigjson, and run
# `docker login`. Stores credentials only in $HOME/.docker/config.json
# (the operator can `docker logout` after to wipe them).
harbor_login() {
  local cred_ns
  if ! cred_ns="$(_harbor_creds_ns)"; then
    echo "ERROR: K8s Secret harbor-push-cred not found in lolday or lolday-jobs." >&2
    echo "       Run 'bash scripts/recover-harbor.sh' first to bootstrap" >&2
    echo "       Harbor projects + the robot account." >&2
    return 1
  fi
  local cfg auth user secret
  cfg="$(kubectl -n "$cred_ns" get secret harbor-push-cred \
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
# Caller contract: NAME is alphanumeric+hyphen, SHA is hex; both are
# interpolated directly into the URL without encoding.
harbor_has_tag() {
  local name=$1 sha=$2
  # M-harbor-sha-validate: $sha is interpolated directly into the Harbor
  # REST query string. A caller that hands in contaminated input (e.g.
  # `; rm -rf /` via a hypothetical future caller that doesn't sanitize)
  # would issue a malformed query and may surface misleading results.
  # Regex-guard to the docker-tag SHA range (short-12 subtree SHA up to
  # full 64-char sha256 digest hex) and fail closed on mismatch. No
  # legitimate caller is affected — build_ref() generates only this form.
  if [[ ! "$sha" =~ ^[0-9a-f]{6,64}$ ]]; then
    echo "ERROR: harbor_has_tag refusing non-SHA arg: $sha" >&2
    return 2
  fi
  local cred_ns
  if ! cred_ns="$(_harbor_creds_ns)"; then
    echo "ERROR: K8s Secret harbor-push-cred not found in lolday or lolday-jobs." >&2
    return 2
  fi
  local cfg auth url status body matches
  cfg="$(kubectl -n "$cred_ns" get secret harbor-push-cred \
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
    matches="${matches:-0}"
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

# harbor_get_digest NAME SHA — print the artifact digest (sha256:<64-hex>)
# of `lolday/<NAME>:<SHA>` from Harbor. Used to pin the helpers.lock entry
# with a content-addressable @sha256:... suffix (H-21-img).
#
# Reuses the same auth + URL pattern as harbor_has_tag — Harbor v2 REST
# API returns the digest in the artifacts list's `.digest` field. Caller
# contract matches harbor_has_tag (NAME alphanum-hyphen, SHA hex).
#
# Deviates from the plan's `docker buildx imagetools inspect` route
# because buildx isn't installed on server30; Harbor REST is the same
# pattern this script already uses (no new dependency).
harbor_get_digest() {
  local name=$1 sha=$2
  # Same regex guard as harbor_has_tag — $sha is interpolated directly
  # into the Harbor REST query string; contaminated input would produce
  # misleading results.
  if [[ ! "$sha" =~ ^[0-9a-f]{6,64}$ ]]; then
    echo "ERROR: harbor_get_digest refusing non-SHA arg: $sha" >&2
    return 2
  fi
  local cred_ns
  if ! cred_ns="$(_harbor_creds_ns)"; then
    echo "ERROR: K8s Secret harbor-push-cred not found in lolday or lolday-jobs." >&2
    return 2
  fi
  local cfg auth url status body digest
  cfg="$(kubectl -n "$cred_ns" get secret harbor-push-cred \
           -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d)"
  auth="$(python3 -c '
import json, sys
print(json.loads(sys.stdin.read())["auths"]["harbor.lolday.svc:80"]["auth"])
' <<<"$cfg")"
  url="http://$HARBOR_HOST_PUSH/api/v2.0/projects/$HARBOR_PROJECT/repositories/$name/artifacts?with_tag=true&q=tags=$sha"
  body="$(mktemp)"
  status="$(curl -sS -o "$body" -w '%{http_code}' \
              -H "Authorization: Basic $auth" "$url" || echo 000)"
  if [ "$status" != "200" ]; then
    cat "$body" >&2
    rm -f "$body"
    echo "ERROR: harbor_get_digest $name $sha returned HTTP $status" >&2
    return 2
  fi
  digest="$(python3 - <"$body" <<'PY'
import json, sys
d = json.load(sys.stdin)
if not isinstance(d, list) or not d:
    sys.exit(1)
print(d[0]["digest"])
PY
)"
  rm -f "$body"
  if [ -z "$digest" ] || ! echo "$digest" | grep -qE '^sha256:[0-9a-f]{64}$'; then
    echo "ERROR: harbor_get_digest $name $sha returned unexpected digest: $digest" >&2
    return 2
  fi
  echo "$digest"
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

# cosign_sign NAME DIGEST — sign the image by digest using the operator's
# Harbor cosign keypair. Closes issue #171 — Kyverno
# verify-lolday-harbor-image-signatures expects a signature attached to
# harbor.lolday.svc:80/lolday/<NAME>@<DIGEST>. Sign-by-digest is the
# mainstream practice (cosign docs: "use the digest, not the tag, to
# ensure the signature is bound to the exact image content"). The signature
# is pushed to the same registry as a sibling tag (cosign default).
#
# The function is a no-op if the operator has not yet run
# scripts/cosign-harbor-init.sh — emits a warning so the operator can act
# before promoting the policy from Audit to Enforce. cosign itself reads
# COSIGN_PASSWORD from env; if unset, cosign will prompt. In an unattended
# context (e.g. a future cron) the operator MUST export it; in interactive
# context we let cosign prompt.
cosign_sign() {
  local name=$1 digest=$2
  local key="${HOME}/.cosign/lolday-harbor.key"
  if [ ! -f "$key" ]; then
    echo "WARN: ${key} not found — image pushed but UNSIGNED." >&2
    echo "WARN: run 'bash scripts/cosign-harbor-init.sh' to bootstrap." >&2
    echo "WARN: ClusterPolicy verify-lolday-harbor-image-signatures will" >&2
    echo "WARN: reject this image in lolday/lolday-jobs/lolday-builds once" >&2
    echo "WARN: promoted from Audit to Enforce." >&2
    return 0
  fi
  if ! command -v cosign >/dev/null 2>&1; then
    echo "WARN: cosign not in PATH — image pushed but UNSIGNED." >&2
    return 0
  fi
  local ref="$HARBOR_HOST_REF/$HARBOR_PROJECT/$name@$digest"
  echo "[sign] $name @ ${digest:0:19}..."
  # COSIGN_PASSWORD is read from env; left unset on the operator's path
  # so cosign prompts interactively. --yes acknowledges the
  # "Are you sure you want to continue?" Rekor confirmation, which is
  # bypassed by the next flag but cosign requires the ack regardless.
  # --tlog-upload=false matches the policy-side `rekor.ignoreTlog: true`:
  # we do NOT enter signatures into the public transparency log because
  # Harbor pushes are operator-private.
  COSIGN_PASSWORD="${COSIGN_PASSWORD:-}" cosign sign \
    --yes \
    --tlog-upload=false \
    --key "$key" \
    "$ref"
}

# ----- orchestrator ----------------------------------------------------

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
      # NOTE: this exit runs inside a process-substitution subshell when
      # called from main()'s `< <(resolve_targets)` loop, so it does NOT
      # propagate. main() validates $ONLY against HELPERS before calling
      # resolve_targets; this branch is defence-in-depth for any future
      # caller that invokes resolve_targets outside main().
      echo "ERROR: --only $ONLY: not in HELPERS=(${HELPERS[*]})" >&2
      exit 1
    fi
  else
    for h in "${HELPERS[@]}"; do echo "$h"; done
  fi
}

# build_ref NAME SHA — print the fully-qualified image ref
# (<HARBOR_HOST_REF>/<HARBOR_PROJECT>/<NAME>:<tag>). When --allow-dirty
# is set, the tag carries a -dirty-<epoch> suffix.
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

  # Validate --only NAME immediately — before any process substitution —
  # so the error exits the main shell (not a discarded subshell).
  if [ -n "$ONLY" ]; then
    local valid=0
    for h in "${HELPERS[@]}"; do
      [ "$h" = "$ONLY" ] && valid=1 && break
    done
    if [ "$valid" -eq 0 ]; then
      echo "ERROR: --only $ONLY: not in HELPERS=(${HELPERS[*]})" >&2
      exit 1
    fi
  fi

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
        # Push tag carries the same SHA (or the dirty-suffixed form via
        # build_ref) — extract from the ref so we don't recompute.
        docker_build_push "$helper" "${ref##*:}"
      fi
      # H-21-img: pin the lock entry with @sha256:<digest>. Harbor REST
      # API replaces the plan's docker-buildx-imagetools-inspect path
      # because buildx isn't a hard dep of this host; Harbor REST is the
      # same pattern the script already uses for harbor_has_tag. Captured
      # for BOTH paths (already-in-Harbor + freshly-built) so the lock
      # entry is always double-anchored.
      local digest
      digest="$(harbor_get_digest "$helper" "${ref##*:}")"
      # Issue #171 — cosign sign by digest. Runs on BOTH paths (freshly
      # built AND already-in-Harbor): re-signing an already-signed digest
      # is idempotent (cosign creates a new signature manifest sibling
      # tag), and for already-in-Harbor images the prior push may have
      # happened before the cosign infra existed (catches the migration
      # backlog on the first post-PR build run). --allow-dirty skips
      # signing because the -dirty-<ts> tag is by definition not what
      # the operator would promote to production.
      if [ "$OPT_ALLOW_DIRTY" -eq 0 ]; then
        cosign_sign "$helper" "$digest"
      fi
      ref="${ref}@${digest}"
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

# ----- entrypoint ------------------------------------------------------

# Allow `source scripts/build-helpers.sh` from tests without firing main.
if [ -z "${LOLDAY_BUILD_HELPERS_SOURCED:-}" ]; then
  main "$@"
fi
