#!/bin/bash
# Fix K3s 401 pulling lolday/* images from Harbor.
# Root cause: /etc/rancher/k3s/registries.yaml declares the mirror but NOT
# credentials, so pulls are anonymous. The `lolday` Harbor project is
# likely private → Harbor returns 401 for anonymous reads.
#
# Fix: toggle the `lolday` project to public (same setting as `detectors`
# per Phase 3 convention). No secret needed at containerd level.
#
# Read ~/.lolday-secrets.env for admin credentials. Runs as the invoking
# user (not root) — only needs kubectl + port-forward.

set -eu

SECRETS=${SECRETS:-$HOME/.lolday-secrets.env}
if [ ! -f "$SECRETS" ]; then
  echo "ERROR: cannot find $SECRETS" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$SECRETS"

# Clean up any stale port-forward
pkill -f "kubectl.*port-forward svc/harbor 8181:" 2>/dev/null || true
sleep 2
kubectl -n lolday port-forward svc/harbor 8181:80 >/tmp/harbor-pf.log 2>&1 &
PF=$!
trap "kill $PF 2>/dev/null || true" EXIT
sleep 4

echo "=== current lolday project metadata ==="
curl -s -u "admin:$HARBOR_ADMIN_PASSWORD" http://localhost:8181/api/v2.0/projects/lolday | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps({'name':d.get('name'),'public':(d.get('metadata') or {}).get('public'),'project_id':d.get('project_id')}, indent=2))"

echo
echo "=== lolday-backend artifacts (check image still exists) ==="
curl -s -u "admin:$HARBOR_ADMIN_PASSWORD" \
  "http://localhost:8181/api/v2.0/projects/lolday/repositories/lolday-backend/artifacts?with_tag=true&page_size=20" | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
for a in d:
    tags = [t['name'] for t in a.get('tags') or []]
    print(f\"  digest={a['digest'][:25]}  tags={tags}\")
"

echo
echo "=== setting lolday project public ==="
RESP=$(curl -s -w "HTTP:%{http_code}" -u "admin:$HARBOR_ADMIN_PASSWORD" \
  -X PUT http://localhost:8181/api/v2.0/projects/lolday \
  -H "Content-Type: application/json" \
  -d '{"metadata":{"public":"true"}}')
echo "  $RESP"

echo
echo "=== verify ==="
curl -s -u "admin:$HARBOR_ADMIN_PASSWORD" http://localhost:8181/api/v2.0/projects/lolday | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('public =', (d.get('metadata') or {}).get('public'))"

echo
echo "=== anonymous pull test (what containerd will do) ==="
curl -sI "http://localhost:8181/v2/lolday/lolday-backend/manifests/phase9.5" | head -5

echo
echo "=== done. Now delete the failed backend pod to trigger re-pull: ==="
echo "  kubectl -n lolday delete pods -l app=backend --force --grace-period=0"
echo "  sleep 20"
echo "  kubectl -n lolday get pods -l app=backend"
