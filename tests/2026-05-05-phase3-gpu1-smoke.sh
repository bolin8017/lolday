#!/usr/bin/env bash
# Smoke: Phase 3 — GPU1 resource profile.
#
# Spec: docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §7 Phase 3
set -euo pipefail

NS=${NS:-lolday}
fail=0

echo "[step 1/2] postgres resource_profile_enum has 'gpu1'"
# Run psql in postgresql-0 pod (avoids backend pod URL-encoding pain with
# special chars in PG_PASSWORD, since psql reads PGPASSWORD env directly).
# PG_PASSWORD must be exported in the caller's shell.
out=$(kubectl -n "${NS}" exec postgresql-0 -- env PGPASSWORD="${PG_PASSWORD:-}" psql -U lolday -d lolday -At -c \
  "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON e.enumtypid=t.oid WHERE t.typname='resource_profile_enum' ORDER BY enumsortorder" 2>/dev/null || true)
case "${out}" in
  *gpu1*) echo "OK: ${out}" ;;
  *) echo "FAIL: enum missing gpu1 (got '${out}')"; fail=1 ;;
esac

echo ""
echo "[step 2/2] backend OpenAPI exposes gpu1"
out=$(kubectl -n "${NS}" exec deploy/backend -c backend -- python3 -c "
import json, urllib.request
d = json.load(urllib.request.urlopen('http://localhost:8000/openapi.json'))
print(json.dumps(d['components']['schemas']['ResourceProfile']))
" 2>/dev/null || true)
case "${out}" in
  *gpu1*) echo "OK: ${out}" ;;
  *) echo "FAIL: ResourceProfile schema missing gpu1 (got '${out}')"; fail=1 ;;
esac

echo ""
[ "${fail}" -eq 0 ] && echo "=== SMOKE PASSED ===" || { echo "=== SMOKE FAILED ==="; exit 1; }
