#!/usr/bin/env bash
set -euo pipefail

SCHEMA_URL=${SCHEMA_URL:-http://localhost:8000/openapi.json}
OUT=src/api/schema.gen.ts

pnpm exec openapi-typescript "$SCHEMA_URL" -o "$OUT"
echo "Generated $OUT"
