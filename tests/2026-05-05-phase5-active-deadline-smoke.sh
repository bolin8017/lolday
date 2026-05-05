#!/usr/bin/env bash
# Smoke: Phase 5 — per-job active_deadline_seconds override.
#
# Spec: docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §7 Phase 5
set -euo pipefail

NS=${NS:-lolday}
fail=0

echo "[step 1/2] job table has active_deadline_seconds column"
out=$(kubectl -n "${NS}" exec deploy/backend -c backend -- python3 -c "
import asyncio, os
import asyncpg

async def main():
    url = os.environ['DATABASE_URL'].replace('+asyncpg', '')
    conn = await asyncpg.connect(url)
    row = await conn.fetchrow(
        \"SELECT column_name FROM information_schema.columns \"
        \"WHERE table_name='job' AND column_name='active_deadline_seconds'\"
    )
    print('OK' if row else 'FAIL')

asyncio.run(main())
" 2>/dev/null || true)
case "${out}" in
  OK) echo "OK" ;;
  *) echo "FAIL: column missing"; fail=1 ;;
esac

echo ""
echo "[step 2/2] OpenAPI schema accepts optional active_deadline_seconds"
out=$(kubectl -n "${NS}" exec deploy/backend -c backend -- python3 -c "
import json, urllib.request
d = json.load(urllib.request.urlopen('http://localhost:8000/openapi.json'))
props = d['components']['schemas']['JobCreate']['properties']
print('OK' if 'active_deadline_seconds' in props else 'FAIL')
" 2>/dev/null || true)
case "${out}" in
  OK) echo "OK" ;;
  *) echo "FAIL: JobCreate schema missing field"; fail=1 ;;
esac

echo ""
[ "${fail}" -eq 0 ] && echo "=== SMOKE PASSED ===" || { echo "=== SMOKE FAILED ==="; exit 1; }
