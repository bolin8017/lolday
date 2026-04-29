#!/bin/bash
# Recover Harbor after a catastrophic data loss (e.g. Stage-4 incident).
#
# Idempotent: safe to re-run. Handles both fresh (no harbor-push-cred
# secret yet) and partially-recovered (robot account exists, secret
# needs rotation) states.
#
# Creates lolday + detectors projects (public), rotates the
# robot$build-pusher secret, upserts the kubernetes harbor-push-cred
# secret, then rebuilds and pushes all core platform images. Uses
# harbor.lolday.svc.cluster.local directly (host /etc/hosts +
# daemon.json insecure-registries already set up in Phase 3).
# Port-forward is only used for admin API calls.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS=${SECRETS:-${REPO_ROOT}/.lolday-secrets.env}
[ -f "$SECRETS" ] || SECRETS="$HOME/.lolday-secrets.env"
[ -f "$SECRETS" ] || { echo "secrets file not found at ${REPO_ROOT}/.lolday-secrets.env or \$HOME/.lolday-secrets.env" >&2; exit 1; }
# shellcheck disable=SC1090
. "$SECRETS"

REPO=${REPO:-/home/bolin8017/Documents/repositories/lolday}
HARBOR_HOST=harbor.lolday.svc.cluster.local:80

# Clean up any stale port-forward, start fresh
pkill -f "kubectl.*port-forward svc/harbor 8181:" 2>/dev/null || true
sleep 2
kubectl -n lolday port-forward svc/harbor 8181:80 >/tmp/harbor-pf.log 2>&1 &
PF=$!
trap "kill $PF 2>/dev/null || true; rm -f /tmp/recover-harbor-patch-*.json 2>/dev/null || true" EXIT
sleep 4

adm="admin:$HARBOR_ADMIN_PASSWORD"
api="http://localhost:8181/api/v2.0"

# ---------------------------------------------------- 1. create projects (idempotent)
for P in lolday detectors; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -u "$adm" "$api/projects/$P")
  if [ "$CODE" = "200" ]; then
    echo "project $P already exists"
  else
    echo "creating project $P…"
    curl -s -u "$adm" -X POST "$api/projects" \
      -H "Content-Type: application/json" \
      -d "{\"project_name\":\"$P\",\"metadata\":{\"public\":\"true\"}}" \
      -w "HTTP:%{http_code}\n"
  fi
done

# ---------------------------------------------------- 2. robot account (upsert + rotate)
#
# On a re-run the robot already exists; Harbor's POST /robots returns 409.
# We detect that and rotate the secret via PATCH /robots/{id}/sec. That's
# the only way to get a fresh secret back — Harbor doesn't expose the
# existing secret via GET.
echo
echo "robot account upsert…"
LIST_JSON=$(curl -sf -u "$adm" "$api/robots?q=name%3Dbuild-pusher")
EXISTING_ID=$(echo "$LIST_JSON" | python3 -c '
import sys, json
try:
    rows = json.loads(sys.stdin.read())
    ids = [r["id"] for r in rows if r.get("name") in ("robot$build-pusher", "build-pusher")]
    print(ids[0] if ids else "")
except Exception:
    print("")
')

if [ -n "$EXISTING_ID" ]; then
  echo "  robot$build-pusher already exists (id=$EXISTING_ID), rotating secret…"
  ROBOT_JSON=$(curl -sf -u "$adm" -X PATCH "$api/robots/$EXISTING_ID/sec" \
    -H "Content-Type: application/json" -d '{}')
else
  echo "  creating robot account…"
  ROBOT_JSON=$(curl -sf -u "$adm" -X POST "$api/robots" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "build-pusher",
      "description": "lolday build-pipeline pusher",
      "duration": -1,
      "disable": false,
      "level": "system",
      "permissions": [
        {"kind":"project","namespace":"lolday","access":[
          {"resource":"repository","action":"push"},
          {"resource":"repository","action":"pull"}]},
        {"kind":"project","namespace":"detectors","access":[
          {"resource":"repository","action":"push"},
          {"resource":"repository","action":"pull"}]},
        {"kind":"project","namespace":"detectors-cache","access":[
          {"resource":"repository","action":"push"},
          {"resource":"repository","action":"pull"}]}
      ]
    }')
fi

# Phase 9.3: ensure existing robots also have detectors-cache perms
# (BuildKit rootless registry-backed layer cache target). Harbor's PATCH
# endpoint only rotates the secret, so we PUT the full robot to update
# permissions. name + level are immutable fields; Harbor rejects edits
# to them but requires them in the body, so we echo the current values
# back verbatim.
if [ -n "$EXISTING_ID" ]; then
  CURRENT=$(curl -sf -u "$adm" "$api/robots/$EXISTING_ID")
  # Idempotency + empty-perms guard. If Harbor is mid-restore and returns
  # a 200 with permissions:[] (legal response for a disabled/purged robot),
  # we must NOT PUT back a body that drops the existing lolday + detectors
  # perms — that would silently break every subsequent build with
  # "unauthorized to access repository: detectors/<name>". Refuse to
  # proceed unless the response carries a non-empty permissions array
  # that already includes lolday + detectors. A bare `[]` is treated as
  # "Harbor is not ready yet" and the deploy should retry later.
  ROBOT_STATE=$(echo "$CURRENT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
perms = d.get("permissions", [])
namespaces = {p.get("namespace") for p in perms if isinstance(p, dict)}
if not perms:
    print("empty")
elif not {"lolday", "detectors"}.issubset(namespaces):
    print("missing-core")
elif "detectors-cache" in namespaces:
    print("already-has-cache")
else:
    print("needs-cache")
')
  case "$ROBOT_STATE" in
    empty)
      echo "  ERROR: robot $EXISTING_ID has empty permissions array — refusing to PUT (would wipe existing grants)" >&2
      exit 1
      ;;
    missing-core)
      echo "  ERROR: robot $EXISTING_ID is missing lolday or detectors grants — refusing to PUT (Harbor state is unexpected)" >&2
      exit 1
      ;;
    already-has-cache)
      : # idempotent no-op; perms already correct
      ;;
    needs-cache)
      echo "  granting detectors-cache perms to existing robot (id=$EXISTING_ID)…"
      NEW_BODY=$(echo "$CURRENT" | python3 -c '
import sys, json
d = json.load(sys.stdin)
d["permissions"].append({
    "kind": "project",
    "namespace": "detectors-cache",
    "access": [
        {"resource": "repository", "action": "push"},
        {"resource": "repository", "action": "pull"},
    ],
})
print(json.dumps({k: d[k] for k in ["name","level","duration","description","disable","editable","expires_at","permissions"] if k in d}))
')
      curl -sf -u "$adm" -X PUT -H "Content-Type: application/json" \
        "$api/robots/$EXISTING_ID" -d "$NEW_BODY" >/dev/null
      # Verify the PUT actually landed. Harbor has been known to return 200
      # on bodies it partially accepts; re-GET and assert the three namespaces
      # are all present.
      POST_STATE=$(curl -sf -u "$adm" "$api/robots/$EXISTING_ID" | python3 -c '
import json, sys
d = json.load(sys.stdin)
ns = {p.get("namespace") for p in d.get("permissions", []) if isinstance(p, dict)}
print("ok" if {"lolday","detectors","detectors-cache"}.issubset(ns) else "partial")
')
      if [ "$POST_STATE" != "ok" ]; then
        echo "  ERROR: PUT /robots/$EXISTING_ID returned 200 but permissions did NOT include all of lolday, detectors, detectors-cache — investigate Harbor logs" >&2
        exit 1
      fi
      echo "  perms updated (verified: lolday + detectors + detectors-cache present)."
      ;;
    *)
      echo "  ERROR: unexpected robot state: $ROBOT_STATE" >&2
      exit 1
      ;;
  esac
fi

# Redacted log — never print the secret. Only show shape of response.
echo "$ROBOT_JSON" | python3 -c '
import sys, json
try:
    d = json.loads(sys.stdin.read())
    redacted = {k: ("<redacted>" if k == "secret" else v) for k, v in d.items()}
    print("  response:", json.dumps(redacted))
except Exception as e:
    print("  unparseable response:", repr(e), file=sys.stderr)
    sys.exit(2)
' >&2

ROBOT_NAME=$(echo "$ROBOT_JSON" | python3 -c '
import sys, json
d = json.loads(sys.stdin.read())
# PATCH response only carries {name, secret}; POST carries same + id.
# If name missing (e.g. PATCH does not echo it on older Harbor), assume
# the canonical form.
print(d.get("name") or "robot$build-pusher")
')
ROBOT_SECRET=$(echo "$ROBOT_JSON" | python3 -c '
import sys, json
d = json.loads(sys.stdin.read())
print(d.get("secret", ""))
')

if [ -z "$ROBOT_SECRET" ]; then
  echo "ERROR: robot response had no secret field — see redacted shape above" >&2
  exit 1
fi

# ---------------------------------------------------- 3. docker login
# docker login BEFORE patching the k8s secret so that if login fails
# (e.g. clock skew, wrong Harbor), we don't leave a freshly-patched
# secret with credentials the cluster can't verify.
echo
echo "docker login $HARBOR_HOST as $ROBOT_NAME…"
echo "$ROBOT_SECRET" | docker login "$HARBOR_HOST" -u "$ROBOT_NAME" --password-stdin

# ---------------------------------------------------- 4. upsert k8s harbor-push-cred
# Build dockerconfig in Python without shell interpolation (avoids
# shell-injection if secret ever contains $/\/`/"). Write the JSON Patch
# to a temp file with 0600 perms so neither the robot secret nor the
# dockerconfigjson appears on any kubectl command line visible to other
# users via /proc/<pid>/cmdline.
echo
echo "upserting kubernetes secret harbor-push-cred…"
DOCKER_CFG_B64=$(
  ROBOT_NAME="$ROBOT_NAME" ROBOT_SECRET="$ROBOT_SECRET" HARBOR_HOST="$HARBOR_HOST" \
  python3 - <<'EOF'
import os, json, base64
auth = base64.b64encode(
    f"{os.environ['ROBOT_NAME']}:{os.environ['ROBOT_SECRET']}".encode()
).decode()
# Register both hostnames — K3s containerd pulls via harbor.lolday.svc:80
# (Service DNS) while host docker uses .cluster.local.
cfg = {"auths": {
    "harbor.lolday.svc:80": {"auth": auth},
    os.environ["HARBOR_HOST"]: {"auth": auth},
}}
print(base64.b64encode(json.dumps(cfg).encode()).decode())
EOF
)

# Upsert: check existence, then PATCH or CREATE.
PATCH_FILE=$(mktemp /tmp/recover-harbor-patch-XXXX.json)
chmod 600 "$PATCH_FILE"
printf '[{"op":"replace","path":"/data/.dockerconfigjson","value":"%s"}]' \
  "$DOCKER_CFG_B64" > "$PATCH_FILE"

if kubectl -n lolday get secret harbor-push-cred >/dev/null 2>&1; then
  kubectl -n lolday patch secret harbor-push-cred --type=json --patch-file "$PATCH_FILE"
else
  echo "  harbor-push-cred not found — creating"
  DECODED=$(mktemp /tmp/recover-harbor-dcfg-XXXX.json)
  chmod 600 "$DECODED"
  echo "$DOCKER_CFG_B64" | base64 -d > "$DECODED"
  kubectl -n lolday create secret generic harbor-push-cred \
    --type=kubernetes.io/dockerconfigjson \
    --from-file=.dockerconfigjson="$DECODED"
  rm -f "$DECODED"
fi
rm -f "$PATCH_FILE"

# ---------------------------------------------------- 5. build + push core images
echo
echo "=== rebuilding + pushing core images ==="

build_push() {
  local SRC=$1 IMG=$2
  echo
  echo ">>> build $IMG ($SRC)"
  docker build --pull -t "$HARBOR_HOST/$IMG" "$SRC"
  echo ">>> push $IMG"
  docker push "$HARBOR_HOST/$IMG"
}

skip_if_missing() {
  local SRC=$1 IMG=$2
  if [ -d "$SRC" ]; then
    build_push "$SRC" "$IMG"
  else
    echo
    echo "WARN: skipping $IMG — directory missing: $SRC" >&2
  fi
}

build_push      "$REPO/backend"                                   "lolday/lolday-backend:phase9.5"
skip_if_missing "$REPO/charts/lolday/helpers/mlflow-server"       "lolday/mlflow-server:v2.20.3"
skip_if_missing "$REPO/frontend"                                  "lolday/lolday-frontend:phase5"

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

echo
echo "=== done. kick backend + wait for pull: ==="
echo "  kubectl -n lolday delete pods -l app=backend --force --grace-period=0"
echo "  kubectl -n lolday get pods -l app=backend -w"
