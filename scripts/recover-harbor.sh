#!/bin/bash
# Recover Harbor after the Stage-4 data loss.
#
# Creates lolday + detectors projects (public), a robot$build-pusher
# account, updates the kubernetes harbor-push-cred secret, then rebuilds
# and pushes all core platform images. Uses harbor.lolday.svc.cluster.local
# directly (host/etc/hosts + daemon.json insecure-registries already set
# up in Phase 3). Port-forward is only used for admin API calls.
set -eu

SECRETS=${SECRETS:-$HOME/.lolday-secrets.env}
. "$SECRETS"

REPO=${REPO:-/home/bolin8017/Documents/repositories/lolday}
HARBOR_HOST=harbor.lolday.svc.cluster.local:80

pkill -f "kubectl.*port-forward svc/harbor 8181:" 2>/dev/null || true
sleep 2
kubectl -n lolday port-forward svc/harbor 8181:80 >/tmp/harbor-pf.log 2>&1 &
PF=$!
trap "kill $PF 2>/dev/null || true" EXIT
sleep 4

adm="admin:$HARBOR_ADMIN_PASSWORD"
api="http://localhost:8181/api/v2.0"

# ---------------------------------------------------- 1. create projects
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

# ---------------------------------------------------- 2. robot account
# Harbor's "system" robot with cross-project push. Name ends up as
# "robot$build-pusher" externally.
echo
echo "creating robot account…"
ROBOT_JSON=$(curl -s -u "$adm" -X POST "$api/robots" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "build-pusher",
    "description": "lolday build-pipeline pusher (regenerated after Stage-4 data loss)",
    "duration": -1,
    "disable": false,
    "level": "system",
    "permissions": [
      {"kind":"project","namespace":"lolday","access":[
        {"resource":"repository","action":"push"},
        {"resource":"repository","action":"pull"}]},
      {"kind":"project","namespace":"detectors","access":[
        {"resource":"repository","action":"push"},
        {"resource":"repository","action":"pull"}]}
    ]
  }')
echo "$ROBOT_JSON"

ROBOT_NAME=$(echo "$ROBOT_JSON" | python3 -c 'import sys,json
try: print(json.loads(sys.stdin.read()).get("name",""))
except: pass')
ROBOT_SECRET=$(echo "$ROBOT_JSON" | python3 -c 'import sys,json
try: print(json.loads(sys.stdin.read()).get("secret",""))
except: pass')

if [ -z "$ROBOT_NAME" ] || [ -z "$ROBOT_SECRET" ]; then
  echo "ERROR: robot creation failed — see response above" >&2
  exit 1
fi

# ---------------------------------------------------- 3. update harbor-push-cred
echo
echo "updating kubernetes secret harbor-push-cred…"
DOCKER_CFG=$(python3 <<EOF
import json, base64
auth = base64.b64encode(b"$ROBOT_NAME:$ROBOT_SECRET").decode()
cfg = {"auths": {"$HARBOR_HOST": {"auth": auth}}}
print(base64.b64encode(json.dumps(cfg).encode()).decode())
EOF
)
kubectl -n lolday patch secret harbor-push-cred \
  --type='json' \
  -p="[{\"op\":\"replace\",\"path\":\"/data/.dockerconfigjson\",\"value\":\"$DOCKER_CFG\"}]"

# ---------------------------------------------------- 4. docker login to Harbor (direct)
echo
echo "docker login $HARBOR_HOST as $ROBOT_NAME…"
echo "$ROBOT_SECRET" | docker login "$HARBOR_HOST" -u "$ROBOT_NAME" --password-stdin

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

build_push "$REPO/backend"                                   "lolday/lolday-backend:phase8"
build_push "$REPO/charts/lolday/helpers/build-helper"        "lolday/build-helper:v2"
[ -d "$REPO/charts/lolday/helpers/job-helper" ]     && build_push "$REPO/charts/lolday/helpers/job-helper"     "lolday/job-helper:v2"
[ -d "$REPO/charts/lolday/helpers/mlflow-server" ]  && build_push "$REPO/charts/lolday/helpers/mlflow-server"  "lolday/mlflow-server:v2.20.3"
[ -d "$REPO/frontend" ]                              && build_push "$REPO/frontend"                              "lolday/lolday-frontend:phase5"

echo
echo "=== done. kick backend + wait for pull: ==="
echo "  kubectl -n lolday delete pods -l app=backend --force --grace-period=0"
echo "  kubectl -n lolday get pods -l app=backend -w"
