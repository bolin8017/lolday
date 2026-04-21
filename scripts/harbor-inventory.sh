#!/bin/bash
# Inspect Harbor: list projects, repositories, and tags for each.
set -eu
SECRETS=${SECRETS:-$HOME/.lolday-secrets.env}
. "$SECRETS"

pkill -f "kubectl.*port-forward svc/harbor 8181:" 2>/dev/null || true
sleep 2
kubectl -n lolday port-forward svc/harbor 8181:80 >/tmp/harbor-pf.log 2>&1 &
PF=$!
trap "kill $PF 2>/dev/null || true" EXIT
sleep 4

echo "=== projects ==="
curl -s -u "admin:$HARBOR_ADMIN_PASSWORD" http://localhost:8181/api/v2.0/projects?page_size=50 | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in d:
    print(f\"  name={p['name']:<20s} id={p['project_id']}  public={(p.get('metadata') or {}).get('public')}  repo_count={p.get('repo_count')}\")
"
echo
echo "=== repos per project ==="
for P in lolday detectors library; do
  echo "--- $P ---"
  curl -s -u "admin:$HARBOR_ADMIN_PASSWORD" "http://localhost:8181/api/v2.0/projects/$P/repositories?page_size=50" | \
    python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if isinstance(d, dict) and 'errors' in d:
        print(f\"  {d['errors']}\")
    else:
        for r in d:
            print(f\"  {r['name']:<40s}  artifacts={r.get('artifact_count')}\")
except Exception as e:
    print(f\"  parse error: {e}\")
"
done
echo
echo "=== artifacts in likely-containing repos ==="
for repo in lolday/lolday-backend library/lolday-backend detectors/lolday-backend; do
  P="${repo%%/*}"
  R="${repo#*/}"
  URL="http://localhost:8181/api/v2.0/projects/$P/repositories/$R/artifacts?with_tag=true&page_size=10"
  echo "--- $repo ---"
  curl -s -u "admin:$HARBOR_ADMIN_PASSWORD" "$URL" | \
    python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if isinstance(d, dict) and 'errors' in d:
        print(f\"  {d['errors'][0]['code']}\")
    else:
        for a in d:
            tags = [t['name'] for t in a.get('tags') or []]
            print(f\"  digest={a['digest'][:25]}  tags={tags}\")
except Exception as e:
    print(f\"  parse error: {e}\")
"
done
