# Backend httpx.Client leak fix — Implementation Plan

> Plan for spec
> [`2026-05-12-backend-httpx-client-leak-fix-design.md`](../specs/2026-05-12-backend-httpx-client-leak-fix-design.md).

## Branch

`fix/backend-httpx-client-leak`

## Step 1 — Failing regression test (TDD)

File: `backend/tests/services/test_gpu_signal.py`

Add `test_query_prometheus_reuses_module_client` (per spec §5.4). Run
`uv run pytest backend/tests/services/test_gpu_signal.py -k reuses` and
confirm it fails against the current code (which constructs a fresh
`httpx.Client` per call, so the assertion would see 10 constructions,
not 1).

## Step 2 — Refactor gpu_signal.py

File: `backend/app/services/gpu_signal.py`

1. Add module-level `_HTTP_CLIENT = httpx.Client(timeout=...)`.
2. Rewrite `_query_prometheus` to use it (drop `with httpx.Client`).
3. Add `close_http_client()` helper.

Per spec §5.1.

## Step 3 — Update existing gpu_signal tests

File: `backend/tests/services/test_gpu_signal.py`

Three tests currently mock `app.services.gpu_signal.httpx.Client` and
unwrap `__enter__.return_value`. Convert them to patch
`gpu_signal._HTTP_CLIENT` directly via a fixture (per spec §5.3).

Affected tests:

- `test_query_prometheus_parses_instant_vector`
- `test_query_prometheus_raises_on_http_error`
- `test_query_prometheus_raises_on_non_success_status_field`

## Step 4 — FastAPI lifespan teardown

File: `backend/app/main.py`

In the `lifespan` async context manager, after the existing
`reconciler_task` / `fifo_task` cleanup, call
`gpu_signal.close_http_client()` (per spec §5.2).

## Step 5 — Run full test suite

```bash
cd backend && uv run pytest
```

All tests should pass. The regression test should now pass too (single
Client construction).

## Step 6 — Revert chart memory mitigation

File: `charts/lolday/templates/backend.yaml`

Remove the inline 7-line comment block and change `memory: 1Gi` →
`memory: 512Mi` (per spec §5.5). The diff is:

```diff
             limits:
               cpu: 500m
-              # Bumped from 512Mi to 1Gi on 2026-05-12 as buffer against an
-              # ongoing memory leak introduced in v0.20.8/9 (suspected
-              # gpu_signal / fifo_scheduler). With 512Mi the pod OOMed every
-              # ~60 min; 1Gi extends the cycle to ~2-3 h while root-cause
-              # investigation is outstanding. Memory file:
-              # ~/.claude/.../memory/project_backend_memory_leak_v0208_or_9.md.
-              # Reduce back to 512Mi once the leak is fixed.
-              memory: 1Gi
+              memory: 512Mi
```

## Step 7 — Lint / format

```bash
pre-commit run --all-files
```

No `--no-verify`. Fix any reported issues at the root.

## Step 8 — Commit

Conventional Commits format (`docs/conventions.md` §2):

```
fix(backend): reuse module-level httpx.Client in gpu_signal

Spec: docs/superpowers/specs/2026-05-12-backend-httpx-client-leak-fix-design.md
Plan: docs/superpowers/plans/2026-05-12-backend-httpx-client-leak-fix.md
```

## Step 9 — Open PR

```bash
gh pr create --title "fix(backend): reuse module-level httpx.Client in gpu_signal" \
  --body "$(cat <<'EOF'
## Summary
- Root-cause fix for backend memory leak introduced in v0.20.8 (5 MiB/min linear growth).
- Each `gpu_signal._query_prometheus` call constructed a fresh `httpx.Client`; glibc malloc arena fragmented ~2 MiB/iter of pages it never returned to the OS. Reusing a module-level Client drops growth from ~2 MiB/iter to ~30 KiB/iter (60× reduction, verified in-pod).
- Reverts the 1Gi memory limit added in PR #130 back to the pre-leak 512Mi.

Spec: docs/superpowers/specs/2026-05-12-backend-httpx-client-leak-fix-design.md
Plan: docs/superpowers/plans/2026-05-12-backend-httpx-client-leak-fix.md
Closes auto-memory entry: project_backend_memory_leak_v0208_or_9.md.

## Test plan
- [x] `uv run pytest backend/tests/services/test_gpu_signal.py` (regression + updated existing tests)
- [x] `uv run pytest` (full suite)
- [x] `pre-commit run --all-files`
- [x] `helm lint charts/lolday`
- [ ] Post-deploy in-pod probe: 60 iter `compute_real_gpu_state` shows <5 MiB total growth
- [ ] Post-deploy 1 h memory observation: `container_memory_working_set_bytes` stays flat under 512Mi limit (no OOM)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

## Step 10 — Code review + merge

Run `/code-review` against the PR. Address any feedback. Merge after green CI.

## Step 11 — Deploy

```bash
bash scripts/deploy.sh
kubectl -n lolday wait pod -l app.kubernetes.io/component=backend --for=condition=Ready --timeout=120s
```

## Step 12 — Verify in production

Per spec §7.3:

```bash
# Sample memory every 5 min for 60 min
for i in $(seq 1 12); do
  date "+%H:%M:%S"
  kubectl -n lolday top pod -l app.kubernetes.io/component=backend --no-headers
  sleep 300
done
```

Expect: RSS stays at ~220 MiB ± 30 MiB across all 12 samples (no linear growth, no OOM).

## Step 13 — Update auto-memory

After 60-minute observation confirms the fix, update
`~/.claude/projects/.../memory/project_backend_memory_leak_v0208_or_9.md`
status from "partially mitigated" → "resolved 2026-05-12".

## Estimated effort

| Step                           | Time        |
| ------------------------------ | ----------- |
| 1-5 (code + tests)             | 30 min      |
| 6 (chart revert)               | 2 min       |
| 7-9 (lint + commit + PR)       | 10 min      |
| 10 (code review iteration)     | 15 min      |
| 11 (deploy)                    | 5 min       |
| 12 (60-min observation, async) | 60 min wall |
| 13 (memory update)             | 2 min       |
| **Total active**               | **~65 min** |
| **Total wall**                 | **~2 h**    |
