# Phase 3: `ResourceProfile.GPU1` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 1-GPU resource profile so two users can run training in parallel on the 2-GPU node (paired with Phase 2 per-user queues whose capability=2 GPUs allows GPU1 OR GPU2). Closes spec §7 Phase 3.

**Architecture:** Postgres enum value addition (`ALTER TYPE … ADD VALUE 'gpu1' BEFORE 'gpu2'`); single-line additions to `ResourceProfile` enum and `_RESOURCE_PROFILE_GPU_COUNT` map (existing fail-loud assertion catches drift); detector container env `MALDET_DISTRIBUTED_STRATEGY` switches from `"ddp"` to `"none"` when `gpu_count <= 1` (DDP across 1 GPU is a no-op and noisy); frontend `schema.gen.ts` hand-stitched (consistent with PR #69 pattern, tracked tech debt §9 #14).

**Tech Stack:** alembic + Postgres ENUM, Python `StrEnum`, FastAPI/Pydantic schema generation, RJSF (frontend renders form options from JSON Schema, no UI component change).

**Spec:** §7 Phase 3.

**Pre-requisite:** Phase 0–2 + Phase 4 merged. Phase 2 per-user queue capability `nvidia.com/gpu: "2"` already lets GPU1 vcjobs schedule.

---

## File map

**New files:**

- `backend/migrations/versions/f1e8115c3234_gpu1_resource_profile.py` — alembic revision (already auto-generated; this plan fills in `upgrade` / `downgrade` bodies).
- `backend/tests/test_models_resource_profile_gpu1.py` — covers enum value + map total.
- `tests/2026-05-05-phase3-gpu1-smoke.sh` — verifies enum migrated + new vcjobs accept gpu1.

**Modified:**

- `backend/app/models/job.py` — add `GPU1 = "gpu1"` to `ResourceProfile` enum + `GPU1: 1` to `_RESOURCE_PROFILE_GPU_COUNT`.
- `backend/app/services/job_spec.py::_detector_container` — set `MALDET_DISTRIBUTED_STRATEGY=none` when `gpu_count <= 1`.
- `frontend/src/api/schema.gen.ts` — hand-stitch `"gpu1"` literal alongside `"standard"` and `"gpu2"`.
- `docs/architecture.md` §9 #14 — note that this hand-stitch lands here too (still tracked tech debt).

**Not touched:** `backend/app/schemas/job.py` (imports the enum directly), `frontend/src/components/...` (RJSF generates options from JSON Schema, no JSX hardcoded).

---

## Tasks

### Task 1 — alembic migration body

`backend/migrations/versions/f1e8115c3234_gpu1_resource_profile.py`:

```python
"""gpu1_resource_profile

Revision ID: f1e8115c3234
Revises: d8928ee4a13d
Create Date: 2026-05-05 12:32:13.460612

"""

from typing import Sequence, Union

from alembic import op


revision: str = "f1e8115c3234"
down_revision: Union[str, Sequence[str], None] = "d8928ee4a13d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add 'gpu1' to resource_profile_enum BEFORE 'gpu2'.

    Postgres ALTER TYPE ADD VALUE cannot run inside a transaction block,
    so wrap in autocommit_block. SQLite (tests) has no named enum types —
    no-op there. Adding BEFORE 'gpu2' keeps the display order natural
    in any UI that ORDERs by enum position.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                "ALTER TYPE resource_profile_enum "
                "ADD VALUE IF NOT EXISTS 'gpu1' BEFORE 'gpu2'"
            )


def downgrade() -> None:
    """Postgres does not support removing enum values. Downgrade is a no-op.

    Rolling back while 'gpu1' is referenced by job rows is unsafe — callers
    must manually migrate those rows to 'standard' or 'gpu2' first.
    """
```

Same pattern as `8a1c2d4e5f60_phase8_gpu2_profile.py`.

### Task 2 — models/job.py

```python
class ResourceProfile(StrEnum):
    STANDARD = "standard"
    GPU1 = "gpu1"   # new — 1 GPU
    GPU2 = "gpu2"
```

```python
_RESOURCE_PROFILE_GPU_COUNT = MappingProxyType(
    {
        ResourceProfile.STANDARD: 0,
        ResourceProfile.GPU1: 1,   # new
        ResourceProfile.GPU2: 2,
    }
)
```

The existing `assert set(_RESOURCE_PROFILE_GPU_COUNT.keys()) == set(ResourceProfile), …` covers correctness — adding the enum value without the map entry would fail import-time.

### Task 3 — services/job_spec.py: gpu_strategy override for single GPU

In `_detector_container`, before composing `env`:

```python
def _detector_container(
    detector_image: str,
    action: str,
    mlflow_tracking_uri: str,
    mlflow_run_id: str,
    mlflow_experiment_id: str,
    gpu_count: int,
    gpu_strategy: str,
) -> dict[str, Any]:
    # Phase 3 — DDP across 1 GPU is a no-op that some maldet detectors
    # warn about. Override here so the container always sees a strategy
    # that matches its allocated GPU count.
    effective_strategy = "none" if gpu_count <= 1 else gpu_strategy
    return {
        ...
        "env": [
            ...
            {"name": "MALDET_GPU_COUNT", "value": str(gpu_count)},
            {"name": "MALDET_DISTRIBUTED_STRATEGY", "value": effective_strategy},
            ...
        ],
        ...
    }
```

### Task 4 — backend tests

`backend/tests/test_models_resource_profile_gpu1.py` (new):

```python
"""Phase 3 — GPU1 resource profile."""

from app.models.job import RESOURCE_PROFILE_GPU_COUNT, ResourceProfile


def test_gpu1_enum_value_present() -> None:
    assert ResourceProfile.GPU1.value == "gpu1"


def test_gpu1_gpu_count_is_one() -> None:
    assert ResourceProfile.GPU1.gpu_count == 1
    assert RESOURCE_PROFILE_GPU_COUNT[ResourceProfile.GPU1] == 1


def test_resource_profile_map_total_over_enum() -> None:
    assert set(RESOURCE_PROFILE_GPU_COUNT.keys()) == set(ResourceProfile)
```

Extend `backend/tests/test_services_job_spec_phase11b.py` with one GPU1 test:

```python
def test_gpu1_profile_emits_strategy_none() -> None:
    """1 GPU → MALDET_DISTRIBUTED_STRATEGY=none (DDP no-op on single GPU)."""
    m = _build(resource_profile=ResourceProfile.GPU1)
    detector = m["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in detector["env"]}
    assert env["MALDET_GPU_COUNT"] == "1"
    assert env["MALDET_DISTRIBUTED_STRATEGY"] == "none"
    limits = detector["resources"]["limits"]
    assert limits["nvidia.com/gpu"] == 1
```

### Task 5 — frontend hand-stitch

`frontend/src/api/schema.gen.ts` line 1259:

```diff
-        ResourceProfile: "standard" | "gpu2";
+        ResourceProfile: "standard" | "gpu1" | "gpu2";
```

(Tech debt #14: should be regenerated via `pnpm gen-api-types`. Hand-stitch is consistent with PR #69's documented pattern.)

### Task 6 — smoke test

`tests/2026-05-05-phase3-gpu1-smoke.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
NS=${NS:-lolday}
fail=0

echo "[step 1/2] postgres resource_profile_enum has 'gpu1'"
out=$(kubectl -n "${NS}" exec deploy/backend -c backend -- python3 -c "
import asyncio, asyncpg, os
async def main():
    url = os.environ['DATABASE_URL'].replace('+asyncpg', '')
    conn = await asyncpg.connect(url)
    rows = await conn.fetch(\"SELECT enumlabel FROM pg_enum e JOIN pg_type t ON e.enumtypid=t.oid WHERE t.typname='resource_profile_enum' ORDER BY enumsortorder\")
    print(','.join(r['enumlabel'] for r in rows))
asyncio.run(main())
" 2>/dev/null)
case "${out}" in
  *gpu1*) echo "OK: ${out}" ;;
  *) echo "FAIL: enum missing gpu1 (got ${out})"; fail=1 ;;
esac

echo ""
echo "[step 2/2] backend OpenAPI exposes gpu1"
out=$(kubectl -n "${NS}" exec deploy/backend -c backend -- python3 -c "
import urllib.request, json
print(json.dumps(json.load(urllib.request.urlopen('http://localhost:8000/openapi.json'))['components']['schemas']['ResourceProfile']))
" 2>/dev/null)
case "${out}" in
  *gpu1*) echo "OK: ${out}" ;;
  *) echo "FAIL: ResourceProfile schema missing gpu1 (got ${out})"; fail=1 ;;
esac

echo ""
[ "${fail}" -eq 0 ] && echo "=== SMOKE PASSED ===" || { echo "=== SMOKE FAILED ==="; exit 1; }
```

### Task 7 — pre-commit + commit + push + PR

Standard. Title: `feat(backend, frontend): phase 3 — ResourceProfile.GPU1 + alembic migration`.

### Task 8 — deploy (deferred, bundled with Phase 5)

helm upgrade ships the new alembic migration via the `alembic-upgrade-hook` Job. Backend code change requires image rebuild (still bundled with Phase 5). Frontend change requires frontend image rebuild.

---

## Self-review checklist

- [ ] migration upgrade uses `autocommit_block`; downgrade is no-op.
- [ ] alembic head still resolves single-line.
- [ ] `_RESOURCE_PROFILE_GPU_COUNT` map updated; assertion still passes.
- [ ] new tests cover enum + map + strategy override.
- [ ] frontend schema.gen.ts hand-stitch is single-line; no other changes.
- [ ] `helm template` renders unchanged (no chart touched).
- [ ] Full `uv run pytest` 487+ passing (Phase 2 baseline) + 4 new tests.
