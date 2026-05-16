#!/usr/bin/env bash
# D3.8 / R5 part 3 — regenerate the OpenAPI snapshot used by
# tests/contract/schema_gen_drift.test.ts. CI calls this then runs
# `git diff --exit-code frontend/tests/fixtures/openapi.snapshot.json`
# to fail loud on backend drift.
set -euo pipefail

SCHEMA_URL=${SCHEMA_URL:-http://localhost:8000/openapi.json}
OUT="tests/fixtures/openapi.snapshot.json"

# Pretty-print via python first (stable, no external dep), then run
# prettier so the formatting matches the pre-commit hook (otherwise the
# CI git-diff guard reports drift on whitespace alone).
curl -fsSL "$SCHEMA_URL" | python3 -m json.tool > "$OUT"
pnpm exec prettier --write --log-level warn --ignore-path ../.gitignore --ignore-path ../.prettierignore "$OUT"
echo "Wrote $OUT (from $SCHEMA_URL)"
