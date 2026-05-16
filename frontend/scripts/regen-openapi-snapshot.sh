#!/usr/bin/env bash
# D3.8 / R5 part 3 — regenerate the OpenAPI snapshot used by
# tests/contract/schema_gen_drift.test.ts. CI calls this then runs
# `git diff --exit-code frontend/tests/fixtures/openapi.snapshot.json`
# to fail loud on backend drift.
set -euo pipefail

SCHEMA_URL=${SCHEMA_URL:-http://localhost:8000/openapi.json}
OUT="tests/fixtures/openapi.snapshot.json"

curl -fsSL "$SCHEMA_URL" | python3 -m json.tool > "$OUT"
echo "Wrote $OUT (from $SCHEMA_URL)"
