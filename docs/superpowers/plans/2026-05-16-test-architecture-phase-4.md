# Test Architecture Phase 4 — Scripts (bats + R6 kick-off), Mutation Testing, Test Telemetry + §10 #30 Heavy-Tier Carryover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Phase 4 of the test architecture redesign — `bats` GHA action + first `tests/bats/` smoke suite over the most regression-prone shell scripts (D4.1); R6 incremental kick-off extracting `scripts/lib/harbor_api.py` and `scripts/lib/helpers_lock.py` out of `scripts/build-helpers.sh` + `scripts/recover-harbor.sh` + `scripts/check-helpers-lock.sh`, each backed by a pytest unit suite (D4.2); weekly `mutation.yml` cron running `mutmut` against the top-10 risk modules with results published as `docs/test-telemetry/mutation-<date>.md` (D4.3); weekly `test-telemetry.yml` cron aggregating every workflow's JUnit XML into a small SQLite DB and a Markdown dashboard with P50/P95/P99 timings + 7-day failure rate + slow-test rank, posting a summary to the Spidey Warnings Discord channel (D4.4); `.claude/rules/scripts-and-ops.md` codification of the "touched script must add lib + test" rule (D4.5); the initial `docs/test-telemetry/dashboard.md` skeleton (D4.6). The plan also folds in the three remaining `docs/architecture.md` §10 #30 deferred items — heavy-tier real-MLflow ACL multi-user (D2.3 #9), audit-log durability on real Postgres (D2.3 #12), JWKS reflector heavy via uvicorn `well-known/jwks` (D2.4 #13) — closing #30 fully.

**Architecture:** Phase 4 sits on top of Phase 1's tiered scaffold, Phase 2's `AUTH_DEV_PERSONAS` + service extraction, and Phase 3's POMs + live-stack E2E (#193 / #196 / #198). Six thrusts:

1. **bats as a first-class test layer** — `bats-core` 1.10.0 + `bats-support` + `bats-assert` (mainstream `bats-core/bats-action@v3` GHA action) wraps shell-script execution so deploy / build / recover paths gain coverage at the per-function granularity that pure `bash -n` cannot. First targets: `scripts/check-helpers-lock.sh` (pure shell, no Docker required), `scripts/build-helpers.sh --help` + `--dry-run` smoke paths (exit-code + stdout assertions; `--dry-run` is the non-network branch already preserved for tests). A new informational workflow `.github/workflows/bats.yml` runs the suite on every PR that touches `scripts/`.
2. **R6 incremental kick-off** — `scripts/lib/harbor_api.py` extracts the four embedded `python3 -<<'PY' ... PY` heredocs out of `scripts/build-helpers.sh` (Harbor v2 REST artifact lookup, digest extraction, robot-auth header decode, dockerconfig parse) into a typed, pytest-covered Python module. `scripts/lib/helpers_lock.py` extracts the lock-file read/write/drift-check logic out of `scripts/build-helpers.sh::write_lock` + `scripts/check-helpers-lock.sh`'s inlined heredoc. Both modules ship with `respx`-driven HTTP tests + tmp_path JSON round-trip tests. The shell scripts then call `python3 -m scripts.lib.harbor_api <verb> <args>` instead of inlining Python — readable, testable, single-source-of-truth.
3. **Mutation testing weekly cron** — `mutmut` runs against the ten high-risk modules listed in spec §1.2 (full list reproduced in `[tool.mutmut]` config). Cron fires Monday 06:00 UTC (14:00 Asia/Taipei), produces `docs/test-telemetry/mutation-<YYYY-MM-DD>.md` via a `scripts/lib/mutation_report.py` script, and opens a tracking issue when the killed-mutant rate on any module drops below 60 % (Phase 4 exit gate; 80 % is the eventual target). The workflow is informational — never blocks PRs.
4. **Test-execution telemetry weekly cron** — `test-telemetry.yml` follows the pattern set by `flaky-tracker.yml` (download JUnit XML artifacts from the last 7 days via `actions/github-script`, then run a Python aggregator). The aggregator is `scripts/lib/test_telemetry.py`; it persists into a small SQLite under `docs/test-telemetry/data.sqlite`, rewrites `docs/test-telemetry/dashboard.md` with per-test 30-day P50 / P95 / P99 durations + 7-day failure rate + flaky candidates (`> 1 %`) + slow tests (`P99 > 30 s`), commits the regenerated dashboard back to `main`, and posts a 5-line summary to the Spidey Warnings Discord channel via the existing `DISCORD_WEBHOOK_URL_WARNING` secret.
5. **§10 #30 heavy-tier closure** — three new files under `backend/tests/heavy/`: `mlflow/test_acl_real_multi_user.py` exercises `experiments_proxy._mlflow_user_filter` against the real MLflow container (two distinct lolday user-IDs each create runs; cross-user GET returns 0 results); `postgres/test_audit_log_durability.py` exercises `services.audit.write_audit_log` against the real Postgres container (JSONB before/after round-trip, append-only invariant, rollback-takes-row-with-it semantics, concurrent writes from two sessions); `auth/test_jwks_reflector.py` spins a uvicorn server serving a minted-RSA-key JWKS at `/.well-known/jwks.json`, calls `_get_jwks_client()` against it, and asserts cache-hit-then-refresh behavior across a freezegun-controlled 600 s TTL window.
6. **Rule + dashboard codification** — `.claude/rules/scripts-and-ops.md` gains a §R6 section stating that any script-touching PR must extract non-trivial logic into a `scripts/lib/` module + add a pytest unit. `docs/test-telemetry/dashboard.md` ships as a skeleton (sections that the cron populates), so the file exists before the first cron firing.

**None of the Phase 4 gates promotes to a required check.** Branch protection stays at the 9 contexts shipped in #194 / #195. The new `bats.yml` / `mutation.yml` / `test-telemetry.yml` gates run informational, fix-forward. Promotion stays an operator decision after the Phase 4 telemetry shows green stability for two consecutive cron runs.

**Tech Stack:** `bats-core` 1.10.0 + `bats-support` + `bats-assert` via `bats-core/bats-action@3.0.0`; `mutmut` 3.x with `pyproject.toml`-driven config; testcontainers-python (already in dev-deps; Phase 1 D1.3 shipped `postgres`/`minio`, Phase 4 reuses the existing `mlflow_url` heavy fixture); `respx` for Harbor REST tests; `freezegun` for the JWKS TTL test; `cryptography` RSA + PyJWT for minting the JWKS reflector keys (`cryptography` is already a backend runtime dep); uvicorn for the reflector ASGI app. No new framework adoption — every tool already lives in `backend/pyproject.toml` `[dependency-groups].dev` except `mutmut` (added in Task 10) and `bats-action` (a GHA action, no backend dep).

---

## Reference

**Source spec:** `docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md` §10 Phase 4 (D4.1 – D4.6), §9 refactor R6, §7.4 coverage targets, §6.7 telemetry pipeline, §1.2 top-10 risk modules.

**Predecessor plans:**

- `docs/superpowers/plans/2026-05-15-test-architecture-phase-1.md` (shipped `745f9ec` / PR #193).
- `docs/superpowers/plans/2026-05-16-test-architecture-phase-2.md` (shipped `1c707af` / PR #196).
- `docs/superpowers/plans/2026-05-16-test-architecture-phase-3.md` (shipped `f8b5572` / PR #198).

**Phase 1+2+3 deliverables Phase 4 builds on:**

- `scripts/lib/flaky_aggregate.py` — Phase 1 D1.13 already shipped a Python module under `scripts/lib/`. Phase 4 R6 extends this directory; the test layout convention (`scripts/tests/lib/test_<module>.py`) is established here.
- `backend/tests/heavy/conftest.py` — Phase 1 D1.8 fixtures: `postgres_container`, `postgres_url`, `real_pg_engine`, `real_pg_session`, `minio_container`, `mlflow_url`. The three Phase 4 heavy tests reuse these directly (no new container per test).
- `backend/tests/integration/services/test_jwks_cache_ttl.py` — Phase 2 D2.4 #14 verifies the JWKS cache is wired (structural). Phase 4 Task 20 adds the missing _behavioral_ side: actual JWT verify via a reflector + cache hit/refresh across TTL boundary.
- `backend/tests/integration/routers/test_mlflow_authz.py` — Phase 2 ACL matrix using respx-mocked MLflow. Phase 4 Task 18 ports a focused subset (multi-user owner / non-owner / admin) to a real MLflow container so the upstream REST contract is locked.
- `backend/tests/integration/routers/test_audit_log.py` — Phase 2 D2.3 audit-log assertions on aiosqlite. Phase 4 Task 19 adds the JSONB + concurrent-write + transactional-atomicity invariants on real PG.
- `.github/workflows/flaky-tracker.yml` — Phase 1 D1.13 pattern (weekly cron, artifact download via `actions/github-script`, Python aggregator). Phase 4 Tasks 16 + 13 reuse the same skeleton verbatim.
- `.github/workflows/backend-slow.yml` — Phase 1 D1.6 pipeline runs `pytest -m heavy` against testcontainers; the three Phase 4 heavy tests automatically pick up because they carry `pytest.mark.heavy`. No workflow change required.
- `scripts/build-helpers.sh` + `scripts/recover-harbor.sh` + `scripts/check-helpers-lock.sh` — the R6 extraction targets, all three thoroughly understood through previous helper-image work; the heredoc bodies map 1:1 to the Python module functions in Tasks 4 + 7.

Phase 5 (optional advanced) gets its own plans if and only if the trigger conditions in spec §10 Phase 5 fire (chaos incident, perf incident, suspected leak, security-research need, or stuck mutation score).

## Phase 1 + 2 + 3 lessons baked into this plan

Ten outcomes from the predecessor sessions inform task design below — captured here so the executing engineer can recognise the pattern without rereading the predecessor plans.

1. **Single-task = one bite-sized commit, around 2–5 minutes of work.** Phase 1 ran 40 tasks, Phase 2 ran 29, Phase 3 ran 29. Phase 4 ships 24 tasks at the same granularity. The per-D-deliverable boundary is preserved in the task header so the engineer can pause and review after each `D4.x` group.

2. **Verify against actual codebase before writing test code.** Phase 2's first dispatch hit `NEEDS_CONTEXT` on five tasks because the plan assumed model shapes that didn't match. Phase 3's dev-seed endpoint had to be rewritten mid-execution because `Detector.git_repo_url` doesn't exist (the actual field is `git_url`), `DatasetConfig.csv_sha256` doesn't exist (`csv_checksum`), and `ModelVersion` doesn't have `name`/`version` fields directly (those live on `RegisteredModel`). Phase 4 was authored after verifying:
   - `scripts/build-helpers.sh` Python heredoc bodies (Task 4 quotes them verbatim — `compute_sha`, `harbor_has_tag`, `harbor_get_digest`, `_harbor_creds_ns`, the dockerconfig auth decode in `harbor_login`).
   - `scripts/recover-harbor.sh` Python heredoc bodies (Task 4 covers the robot list + permissions sync ones too).
   - `scripts/check-helpers-lock.sh`'s drift-check inline (Task 7 lifts it).
   - `backend/app/auth/cf_access.py:184-191` — `_get_jwks_client()` uses `pyjwt.PyJWKClient(url, lifespan=..., cache_jwk_set=True)` wrapped in `lru_cache(maxsize=1)`. Task 20 mints a JWKS reflector against this exact construction path.
   - `backend/app/services/audit.py` — `write_audit_log(session, *, actor_id, action, target_type, target_id, before, after)` takes a session and does NOT commit. `backend/app/models/audit.py::AuditLog` uses `_JSONB = JSONB().with_variant(JSON(), "sqlite")` so real-PG hits JSONB while aiosqlite tests hit plain JSON — Task 19 exercises the real path.
   - `backend/app/routers/experiments_proxy.py:61-81` — `_mlflow_user_filter` builds `f"tags.\"lolday.user_id\" = '{user_id!s}'"` after a UUID-shape guard. Task 18 verifies real MLflow respects this filter (MLflow REST has accepted `tags.<key>` filter syntax since 1.x).
   - `backend/tests/heavy/conftest.py` — `real_pg_session` is a `pytest_asyncio.fixture` with per-test transaction rollback; `mlflow_url` is a session-scoped string. Heavy tests opt out of the autouse MLflow mock via `@pytest.mark.no_mock_mlflow`.

3. **`scripts/tests/lib/test_<module>.py` is the pytest layout for `scripts/lib/` modules.** The path convention is set by spec §9 R6. pytest discovers them by passing the path explicitly: `cd backend && uv run pytest ../scripts/tests/lib/`. Task 5 adds `scripts/tests/__init__.py` + `scripts/tests/lib/__init__.py` (empty marker files) so pytest treats the tree as a package. Task 9 extends `backend-fast.yml` with the new invocation; no separate workflow needed.

4. **bats lives at top-level `tests/bats/`** per project layout (`CLAUDE.md` project table: `tests/phase7/` is the existing shell-smoke neighbourhood). Workflow lives at `.github/workflows/bats.yml`. The setup-bats action is `bats-core/bats-action@3.0.0` (the official one; check actual SHA when pinning).

5. **mutmut config goes in `backend/pyproject.toml` `[tool.mutmut]`** — mutmut 3.x reads pyproject directly. `paths_to_mutate` takes a list of module-path strings; `tests_dir` points to where pytest runs. Cron downloads the project, runs `uv run mutmut run`, then `uv run mutmut results` to get the JSON breakdown.

6. **Branch protection stays at 9 contexts; no Phase 4 gate is promoted.** `bats.yml`, `mutation.yml`, and `test-telemetry.yml` all run informational. Promotion (e.g., bats becomes required) is a separate operator step after two consecutive green telemetry runs (the natural decision window is mid-Phase 5 design).

7. **Skip-companion masking** — `frontend.yml` has `frontend-skip.yml`, `helm.yml` has `helm-skip.yml`, etc. The new Phase 4 workflows (`bats.yml`) use `paths` filters; if a PR doesn't touch the filtered paths, the gate doesn't fire AND is not a required check, so no skip-companion is needed. `mutation.yml` and `test-telemetry.yml` are `schedule:`-driven (weekly cron), so PRs never see them — no skip-companion needed.

8. **DOCS_ENABLED gates `/openapi.json`** — the Phase 3 lesson. Phase 4's mutation cron runs `pytest` (not uvicorn against `/openapi.json`), so `DOCS_ENABLED` does not need to be set for the mutation workflow. For the heavy-tier JWKS reflector test (Task 20), the reflector serves its own `/.well-known/jwks.json` — it doesn't depend on backend `/openapi.json`.

9. **Heavy tests need Docker daemon — wire `pytest.importorskip` for local dev.** Local dev environments often lack the Docker socket; CI ubuntu-24.04 has it. Tasks 18 + 19 use `pytest.importorskip("testcontainers")` at module top to skip cleanly when testcontainers is unavailable; Task 20 uses `pytest.importorskip("uvicorn")` (uvicorn IS in the runtime deps, but the heavy-tier convention is to make every heavy module skip-friendly locally).

10. **Markdown commit from a workflow goes through `stefanzweifel/git-auto-commit-action`** — mainstream GitHub Action for committing back from a workflow. Pinned by SHA per `.claude/rules/github-actions.md`. Task 16 wires this into `test-telemetry.yml` to commit the regenerated `dashboard.md` back to `main`.

---

## Prerequisites (must be in place before Phase 4 starts)

- [x] **#193 + #194 + #195 + #196 + #198 merged** — branch protection on 9 contexts, skip-companions, Phase 1 + Phase 2 + Phase 3 deliverables. Verified by `gh pr list --state merged --base main -L 5`.
- [x] **`scripts/lib/flaky_aggregate.py`** already exists (Phase 1 D1.13). Phase 4 extends the directory.
- [x] **`backend/tests/heavy/conftest.py`** ships `postgres_container` / `real_pg_session` / `minio_container` / `mlflow_url` fixtures (Phase 1 D1.8). Phase 4 reuses verbatim.
- [x] **`.github/workflows/backend-slow.yml`** runs `pytest -m heavy`. Phase 4 heavy tests inherit the runner; no workflow edit required.
- [x] **`.github/workflows/flaky-tracker.yml`** is the cron-workflow shape Phase 4 copies for `mutation.yml` and `test-telemetry.yml`.
- [x] **`backend/pyproject.toml`** includes `respx`, `freezegun`, `testcontainers[postgres,minio]`, `mlflow`. Phase 4 only needs to add `mutmut` (Task 10).
- [x] **`docs/architecture.md` §10 #30** flagged as partial-close, with the three heavy deferrals named. Phase 4 closes it.

If any of the above is missing or red, **stop** and resolve before starting Phase 4 — every task below assumes the Phase 3 shape.

The architecture.md §10 #30 "Phase 2 deferred follow-ups (heavy testcontainers tier still pending)" is **closed by this plan** via Tasks 18 + 19 + 20, then resolution noted in Task 23.

---

## File Structure

**New files**

Scripts / lib + tests:

- `scripts/lib/__init__.py` (Task 4 — already implicit; Task 4 makes explicit)
- `scripts/lib/harbor_api.py` (Task 4 — Harbor v2 REST helpers extracted from `build-helpers.sh` + `recover-harbor.sh`)
- `scripts/lib/helpers_lock.py` (Task 7 — `helpers.lock` JSON read/write/drift-check, extracted from `build-helpers.sh::write_lock` + `check-helpers-lock.sh`)
- `scripts/lib/mutation_report.py` (Task 12 — wraps `mutmut results` into the `docs/test-telemetry/mutation-<date>.md` writer)
- `scripts/lib/test_telemetry.py` (Task 14 — JUnit XML aggregator + dashboard regenerator + Discord summary builder)
- `scripts/tests/__init__.py` (Task 5 — empty marker)
- `scripts/tests/lib/__init__.py` (Task 5 — empty marker)
- `scripts/tests/lib/test_harbor_api.py` (Task 5 — respx-based unit tests for harbor_api)
- `scripts/tests/lib/test_helpers_lock.py` (Task 8 — tmp_path JSON round-trip for helpers_lock)
- `scripts/tests/lib/test_mutation_report.py` (Task 12 — fixture JSON in, markdown out)
- `scripts/tests/lib/test_test_telemetry.py` (Task 15 — fixture JUnit XML in, dashboard + summary out)

bats tests:

- `tests/bats/check_helpers_lock_smoke.bats` (Task 2 — pure shell, no Docker)
- `tests/bats/build_helpers_smoke.bats` (Task 3 — `--help` + `--dry-run` paths)
- `tests/bats/.gitignore` (Task 2 — ignores `bats-core/` checkout subdirectory if present)

Heavy backend tests (§10 #30 closure):

- `backend/tests/heavy/auth/__init__.py` (Task 20)
- `backend/tests/heavy/auth/test_jwks_reflector.py` (Task 20 — D2.4 #13)
- `backend/tests/heavy/mlflow/test_acl_real_multi_user.py` (Task 18 — D2.3 #9)
- `backend/tests/heavy/postgres/test_audit_log_durability.py` (Task 19 — D2.3 #12)

Workflows + dashboard:

- `.github/workflows/bats.yml` (Task 3 — informational on PRs touching `scripts/` or `tests/bats/`)
- `.github/workflows/mutation.yml` (Task 13 — weekly Monday 06:00 UTC)
- `.github/workflows/test-telemetry.yml` (Task 16 — weekly Monday 06:30 UTC, 30 min after `mutation.yml`)
- `docs/test-telemetry/.gitignore` (Task 17 — ignores `data.sqlite` cache + transient `mutation-*.md` if any)
- `docs/test-telemetry/dashboard.md` (Task 17 — skeleton populated by cron)
- `docs/test-telemetry/README.md` (Task 17 — explains the directory's role)

**Modified files**

- `scripts/build-helpers.sh` — replace four Python heredocs with `python3 -m scripts.lib.harbor_api <verb>` calls (Task 6); replace `write_lock` body with `python3 -m scripts.lib.helpers_lock write …` (Task 9).
- `scripts/recover-harbor.sh` — replace four embedded `python3 -<<'PY' ... PY` blocks (robot-list parsing, permissions sync, dockerconfig builder, redact-secret printer) with `python3 -m scripts.lib.harbor_api <verb>` invocations (Task 6).
- `scripts/check-helpers-lock.sh` — replace the inline drift-check heredoc with `python3 -m scripts.lib.helpers_lock check-drift …` (Task 9).
- `backend/pyproject.toml` — add `mutmut>=3,<4` to `[dependency-groups].dev`; add `[tool.mutmut]` config block (Tasks 10 + 11).
- `.github/workflows/backend-fast.yml` — add a `pytest ../scripts/tests/lib/` step after the existing fast-tier pytest (Task 9 final step).
- `.claude/rules/scripts-and-ops.md` — add `## R6 — Touched script must add lib + test` section (Task 21).
- `docs/architecture.md` §10 #30 — flip to "resolved 2026-05-16 in Phase 4 (#NNN)" (Task 23).

**Deleted files**

- None. R6 is non-destructive: bash callers swap from inline heredocs to module invocations; nothing is dropped.

---

## Tasks

### Task 1: Create `tests/bats/` directory + helper-library checkout convention (D4.1 — part 1 of 3)

**Files:**

- Create: `tests/bats/.gitignore`
- Create: `tests/bats/README.md`

- [ ] **Step 1: Create the `tests/bats/` directory + `.gitignore`**

```bash
mkdir -p tests/bats
```

Open `tests/bats/.gitignore`:

```gitignore
# Local bats helper-library checkouts (CI fetches via setup-bats action).
bats-core/
bats-support/
bats-assert/
```

- [ ] **Step 2: Write `tests/bats/README.md`**

Open `tests/bats/README.md`:

````markdown
# bats — shell script smoke tests

Phase 4 D4.1 (test architecture redesign spec
`docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md` §10).

CI runs every `.bats` file in this directory via `bats-core/bats-action`
(see `.github/workflows/bats.yml`). The suite is informational — does
not block PRs today; promotion to required is an operator decision
after two consecutive green telemetry runs.

## Running locally

The mainstream pattern (Bats docs) is to vendor `bats-support` +
`bats-assert` next to the test file. The `.gitignore` here keeps a
local checkout out of git.

```bash
# one-time setup (in this directory)
git clone --depth 1 https://github.com/bats-core/bats-core.git
git clone --depth 1 https://github.com/bats-core/bats-support.git
git clone --depth 1 https://github.com/bats-core/bats-assert.git

# run all suites
./bats-core/bin/bats tests/bats/
```
````

CI uses the official action so no checkout is needed there.

## Conventions

- One `.bats` file per script under `scripts/` (suffix `_smoke.bats` for
  the basic exit-code-and-flag suite).
- Heavy paths (real Docker, real Harbor) belong in pytest heavy tier,
  not bats. bats covers the orchestration shell — flags, dry-run,
  argument parsing, env-var handling, file IO.
- Add a `setup()` block that exports `LOLDAY_REPO_ROOT_OVERRIDE` to a
  scratch fixture repo if the script-under-test reads from the git
  tree (see `build_helpers_smoke.bats`).

````

- [ ] **Step 3: Commit**

```bash
git add tests/bats/.gitignore tests/bats/README.md
git commit -m "$(cat <<'EOF'
test(bats): scaffold tests/bats/ directory for shell-script smoke tier

Phase 4 D4.1 part 1. Sets up the convention so subsequent .bats files
land in a consistent home. README documents the bats-support +
bats-assert vendor pattern; .gitignore keeps the local checkouts out
of git (CI uses bats-core/bats-action instead).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 D4.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
````

---

### Task 2: First bats smoke — `scripts/check-helpers-lock.sh` (D4.1 — part 2 of 3)

`check-helpers-lock.sh` is the natural first target — pure shell, no
Docker/Harbor, two well-defined exit paths (env-bypass and drift-check)
that map cleanly to bats `@test` blocks.

**Files:**

- Create: `tests/bats/check_helpers_lock_smoke.bats`

- [ ] **Step 1: Write the bats suite**

Open `tests/bats/check_helpers_lock_smoke.bats`:

```bash
#!/usr/bin/env bats
# D4.1 — smoke for scripts/check-helpers-lock.sh
#
# Exit codes:
#   0 — bypass via LOLDAY_SKIP_HELPERS_LOCK_CHECK=1; or lock matches HEAD
#   1 — lock missing, or lock drifts from HEAD, or missing @sha256 pin

setup() {
  REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
  SCRIPT="${REPO_ROOT}/scripts/check-helpers-lock.sh"
}

@test "exits 0 when LOLDAY_SKIP_HELPERS_LOCK_CHECK=1" {
  run env LOLDAY_SKIP_HELPERS_LOCK_CHECK=1 bash "${SCRIPT}"
  [ "$status" -eq 0 ]
}

@test "exits 1 when lock file is missing" {
  TMP="$(mktemp -d)"
  # Build a fixture repo: just enough git + chart subtree to make the
  # `git rev-parse HEAD:charts/...` calls inside the script return something,
  # but with no helpers.lock file at all.
  cd "$TMP"
  git init -q
  git config user.email t@t
  git config user.name t
  mkdir -p charts/lolday/helpers/build-helper charts/lolday/helpers/job-helper
  echo dummy > charts/lolday/helpers/build-helper/x
  echo dummy > charts/lolday/helpers/job-helper/x
  git add -A
  git commit -qm seed
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "helpers.lock missing"
}

@test "exits 1 when lock SHA disagrees with HEAD subtree" {
  TMP="$(mktemp -d)"
  cd "$TMP"
  git init -q
  git config user.email t@t
  git config user.name t
  mkdir -p charts/lolday/helpers/build-helper charts/lolday/helpers/job-helper
  echo a > charts/lolday/helpers/build-helper/x
  echo a > charts/lolday/helpers/job-helper/x
  git add -A && git commit -qm seed
  # Fake lock with the wrong SHA (000000000000) and a syntactically valid digest.
  cat > charts/lolday/helpers.lock <<JSON
{
  "build_helper": "harbor.lolday.svc:80/lolday/build-helper:000000000000@sha256:$(printf '%064d' 0)",
  "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:000000000000@sha256:$(printf '%064d' 0)"
}
JSON
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "drift detected"
}

@test "exits 1 when lock entry missing @sha256 pin" {
  TMP="$(mktemp -d)"
  cd "$TMP"
  git init -q
  git config user.email t@t && git config user.name t
  mkdir -p charts/lolday/helpers/build-helper charts/lolday/helpers/job-helper
  echo a > charts/lolday/helpers/build-helper/x
  echo a > charts/lolday/helpers/job-helper/x
  git add -A && git commit -qm seed
  # Compute the right SHAs so tag check passes; deliberately omit @sha256.
  BSHA=$(git rev-parse --short=12 HEAD:charts/lolday/helpers/build-helper)
  JSHA=$(git rev-parse --short=12 HEAD:charts/lolday/helpers/job-helper)
  cat > charts/lolday/helpers.lock <<JSON
{
  "build_helper": "harbor.lolday.svc:80/lolday/build-helper:${BSHA}",
  "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:${JSHA}"
}
JSON
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "missing @sha256"
}
```

- [ ] **Step 2: Run the suite locally to confirm it executes**

```bash
# install bats locally if not yet installed (one-time)
git -C tests/bats clone --depth 1 https://github.com/bats-core/bats-core.git
./tests/bats/bats-core/bin/bats tests/bats/check_helpers_lock_smoke.bats
```

Expected: 4 passing tests. If bats is unavailable, skip this step — CI verifies via Task 3.

- [ ] **Step 3: Commit**

```bash
git add tests/bats/check_helpers_lock_smoke.bats
git commit -m "$(cat <<'EOF'
test(bats): smoke for scripts/check-helpers-lock.sh

Phase 4 D4.1 part 2. Four cases: bypass env, missing lock file,
drifted SHA, missing @sha256 pin. Uses LOLDAY_REPO_ROOT_OVERRIDE +
ephemeral git fixture so the script under test sees a deterministic
repo state without touching the real charts/lolday/helpers.lock.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 D4.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `bats.yml` GHA workflow + `--help` smoke for `build-helpers.sh` (D4.1 — part 3 of 3)

**Files:**

- Create: `tests/bats/build_helpers_smoke.bats`
- Create: `.github/workflows/bats.yml`

- [ ] **Step 1: Write the build-helpers smoke**

Open `tests/bats/build_helpers_smoke.bats`:

```bash
#!/usr/bin/env bats
# D4.1 — smoke for scripts/build-helpers.sh
#
# Covers the non-network paths: --help (no side effects) and the argv
# error branches (unknown flag, --only with no NAME, --only with NAME
# not in HELPERS=()). The --dry-run path needs LOLDAY_REPO_ROOT_OVERRIDE
# pointed at a git tree that has charts/lolday/helpers/{build,job}-helper
# subtrees, so we set one up via setup().

setup() {
  REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
  SCRIPT="${REPO_ROOT}/scripts/build-helpers.sh"
  # Fixture repo for --dry-run.
  TMP="$(mktemp -d)"
  pushd "$TMP" >/dev/null
  git init -q
  git config user.email t@t && git config user.name t
  mkdir -p charts/lolday/helpers/build-helper charts/lolday/helpers/job-helper
  echo from-fixture > charts/lolday/helpers/build-helper/Dockerfile
  echo from-fixture > charts/lolday/helpers/job-helper/Dockerfile
  git add -A && git commit -qm seed
  popd >/dev/null
  export LOLDAY_REPO_ROOT_OVERRIDE="$TMP"
}

teardown() {
  rm -rf "$TMP"
}

@test "--help prints usage and exits 0" {
  run bash "${SCRIPT}" --help
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "Usage:"
  echo "$output" | grep -q -- "--dry-run"
}

@test "unknown flag exits 1 with error" {
  run bash "${SCRIPT}" --bogus-flag
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "unknown flag"
}

@test "--only without a NAME exits 1" {
  run bash "${SCRIPT}" --only
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "requires a NAME"
}

@test "--only NAME not in HELPERS exits 1 from main shell" {
  run bash "${SCRIPT}" --only does-not-exist --dry-run
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "not in HELPERS"
}

@test "--dry-run prints the helper refs without calling docker" {
  run bash "${SCRIPT}" --dry-run
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "\[dry-run\] build-helper"
  echo "$output" | grep -q "\[dry-run\] job-helper"
}
```

- [ ] **Step 2: Write the `bats.yml` workflow**

Open `.github/workflows/bats.yml`:

```yaml
name: bats

on:
  pull_request:
    branches: [main]
    paths:
      - "scripts/**"
      - "tests/bats/**"
      - ".github/workflows/bats.yml"
  push:
    branches: [main]
    paths:
      - "scripts/**"
      - "tests/bats/**"
      - ".github/workflows/bats.yml"

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  bats:
    name: bats smoke
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2

      - name: Setup bats
        uses: bats-core/bats-action@472edde1138d59aca53ff162fb8d996666d21e4a # 3.0.0
        with:
          bats-version: 1.10.0
          support-install: true
          assert-install: true
          detik-install: false
          file-install: false

      - name: Run bats suites
        run: bats tests/bats/
```

- [ ] **Step 3: Commit**

```bash
git add tests/bats/build_helpers_smoke.bats .github/workflows/bats.yml
git commit -m "$(cat <<'EOF'
ci(bats): add bats.yml workflow + build-helpers.sh smoke

Phase 4 D4.1 part 3. New bats.yml runs every PR/push that touches
scripts/, tests/bats/, or the workflow itself. Uses
bats-core/bats-action@3.0.0 (mainstream official action) with the
support + assert helper libraries; bats 1.10.0 is the current stable
line. Concurrency cancels the prior PR run on a new push;
permissions: contents: read only.

build-helpers.sh smoke covers --help / --bogus-flag / --only NAME /
--only with missing NAME / --dry-run paths via an ephemeral
LOLDAY_REPO_ROOT_OVERRIDE fixture repo. Heavy paths (docker push,
Harbor REST) stay in pytest heavy tier — bats covers only the shell
orchestration layer.

bats.yml is informational; not added to branch protection per
Phase 4 plan (promotion is an operator decision after two consecutive
green telemetry runs).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 D4.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Extract `scripts/lib/harbor_api.py` from build-helpers.sh + recover-harbor.sh (D4.2 — part 1 of 6)

**Files:**

- Create: `scripts/lib/__init__.py` (if not already a package marker)
- Create: `scripts/lib/harbor_api.py`

- [ ] **Step 1: Verify scripts/lib/ is a package**

```bash
test -f scripts/lib/__init__.py || touch scripts/lib/__init__.py
ls scripts/lib/
```

Expected output: `__init__.py`, `flaky_aggregate.py`.

- [ ] **Step 2: Write `scripts/lib/harbor_api.py`**

Open `scripts/lib/harbor_api.py`:

```python
"""Harbor v2 REST API helpers used by scripts/{build-helpers,recover-harbor}.sh.

Phase 4 D4.2 R6 extraction. Replaces four `python3 -<<'PY' ... PY` heredocs
that were inlined into bash. The shell scripts now call:

    python3 -m scripts.lib.harbor_api <verb> [args...]

verbs:
    creds-namespace             — print the K8s namespace holding the
                                  harbor-push-cred Secret (lolday | lolday-jobs)
    decode-dockerconfig <file>  — read dockerconfigjson from <file>,
                                  print the "robot$build-pusher:<secret>"
                                  auth tuple (base64-decoded)
    build-dockerconfig <user> <secret> <host>
                                — build the dockerconfigjson body
                                  (registers both .svc:80 and the host alias)
                                  and print it base64-encoded
    has-tag <name> <sha>        — exit 0 if Harbor serves
                                  lolday/<name>:<sha>, 1 if 404, 2 on error
    get-digest <name> <sha>     — print the artifact's @sha256:<hex>
                                  digest; exit 2 on error
    parse-robot-list            — read the JSON-array response from
                                  GET /robots?q=name=build-pusher on stdin,
                                  print the id of the matching robot
                                  (empty string + exit 0 if none)
    robot-state                 — read a GET /robots/{id} response on stdin,
                                  print one of: empty | missing-core
                                  | already-has-cache | needs-cache
    add-cache-perm              — read a GET /robots/{id} response on stdin,
                                  emit the PUT body that appends the
                                  detectors-cache repository:push+pull
                                  permission
    redact-robot-response       — read a POST/PATCH /robots[/id] response
                                  on stdin, print the shape with the secret
                                  field replaced by "<redacted>"

Reusable subprocess parameters are env-driven so the bash side never
puts secrets on the command line:

    HARBOR_CRED_NS              — explicit namespace override for has-tag /
                                  get-digest (skips the lolday/lolday-jobs
                                  probe)
    HARBOR_HOST                 — base Harbor host:port (default
                                  harbor.lolday.svc.cluster.local:80)
    HARBOR_PROJECT              — Harbor project (default "lolday")

Tested via scripts/tests/lib/test_harbor_api.py with respx for HTTP
and monkeypatched subprocess for the kubectl side.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from typing import Any

import httpx

DEFAULT_HARBOR_HOST = "harbor.lolday.svc.cluster.local:80"
DEFAULT_HARBOR_PROJECT = "lolday"
CANDIDATE_NAMESPACES = ("lolday", "lolday-jobs")


def _harbor_host() -> str:
    return os.environ.get("HARBOR_HOST", DEFAULT_HARBOR_HOST)


def _harbor_project() -> str:
    return os.environ.get("HARBOR_PROJECT", DEFAULT_HARBOR_PROJECT)


def _kubectl_get_secret(namespace: str, name: str) -> str | None:
    """Return the .dockerconfigjson value (base64-decoded JSON string)
    of <namespace>/<name>, or None if the Secret does not exist."""
    result = subprocess.run(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "secret",
            name,
            "-o",
            "jsonpath={.data.\\.dockerconfigjson}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return base64.b64decode(result.stdout.strip()).decode()


def creds_namespace() -> str:
    """Find the K8s namespace holding harbor-push-cred. Honour
    HARBOR_CRED_NS if set."""
    override = os.environ.get("HARBOR_CRED_NS")
    if override:
        return override
    for ns in CANDIDATE_NAMESPACES:
        if _kubectl_get_secret(ns, "harbor-push-cred") is not None:
            return ns
    raise RuntimeError(
        "harbor-push-cred Secret not found in any of: " + ", ".join(CANDIDATE_NAMESPACES)
    )


def decode_dockerconfig(cfg_json: str, *, host_key: str | None = None) -> str:
    """Decode the auth tuple ('robot$build-pusher:<secret>') from a
    dockerconfigjson body. Pick the .svc:80 entry by default — see
    docstring on build-helpers.sh::harbor_login for why."""
    data = json.loads(cfg_json)
    auths = data.get("auths", {})
    key = host_key or "harbor.lolday.svc:80"
    if key not in auths:
        # Fall back to whatever single entry the Secret carries.
        if len(auths) != 1:
            raise KeyError(
                f"dockerconfigjson missing {key!r} and has {len(auths)} other "
                f"auths entries; cannot disambiguate"
            )
        key = next(iter(auths))
    encoded = auths[key]["auth"]
    return base64.b64decode(encoded).decode()


def build_dockerconfig(user: str, secret: str, host_alias: str) -> str:
    """Build a dockerconfigjson registering BOTH harbor.lolday.svc:80
    (K3s containerd) and host_alias (host docker). Return base64-encoded
    JSON ready for use as Secret.data.\\.dockerconfigjson."""
    auth = base64.b64encode(f"{user}:{secret}".encode()).decode()
    cfg = {
        "auths": {
            "harbor.lolday.svc:80": {"auth": auth},
            host_alias: {"auth": auth},
        }
    }
    return base64.b64encode(json.dumps(cfg).encode()).decode()


def _harbor_artifact_url(name: str, sha: str) -> str:
    return (
        f"http://{_harbor_host()}/api/v2.0/projects/{_harbor_project()}"
        f"/repositories/{name}/artifacts?with_tag=true&q=tags={sha}"
    )


def _auth_header_from_creds() -> str:
    ns = creds_namespace()
    cfg = _kubectl_get_secret(ns, "harbor-push-cred")
    if cfg is None:
        raise RuntimeError(f"harbor-push-cred unexpectedly missing in {ns}")
    auth = json.loads(cfg)["auths"]["harbor.lolday.svc:80"]["auth"]
    return f"Basic {auth}"


def has_tag(name: str, sha: str, *, client: httpx.Client | None = None) -> bool:
    """Return True iff Harbor serves <project>/<name>:<sha>. 404 → False;
    any other non-200 → raise RuntimeError."""
    if not _is_safe_sha(sha):
        raise ValueError(f"refusing non-SHA arg: {sha!r}")
    url = _harbor_artifact_url(name, sha)
    headers = {"Authorization": _auth_header_from_creds()}
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=30.0)
    try:
        resp = client.get(url, headers=headers)
    finally:
        if owns_client:
            client.close()
    if resp.status_code == 200:
        body = resp.json()
        return isinstance(body, list) and len(body) > 0
    if resp.status_code == 404:
        return False
    raise RuntimeError(f"has-tag {name} {sha} HTTP {resp.status_code}: {resp.text}")


def get_digest(name: str, sha: str, *, client: httpx.Client | None = None) -> str:
    """Return the @sha256:<hex> digest for <project>/<name>:<sha>. Raise
    RuntimeError on HTTP error or unexpected payload shape."""
    if not _is_safe_sha(sha):
        raise ValueError(f"refusing non-SHA arg: {sha!r}")
    url = _harbor_artifact_url(name, sha)
    headers = {"Authorization": _auth_header_from_creds()}
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=30.0)
    try:
        resp = client.get(url, headers=headers)
    finally:
        if owns_client:
            client.close()
    if resp.status_code != 200:
        raise RuntimeError(
            f"get-digest {name} {sha} HTTP {resp.status_code}: {resp.text}"
        )
    body = resp.json()
    if not isinstance(body, list) or not body:
        raise RuntimeError(f"get-digest {name} {sha}: empty artifact list")
    digest = body[0].get("digest", "")
    if not _is_sha256_digest(digest):
        raise RuntimeError(f"get-digest {name} {sha}: unexpected digest {digest!r}")
    return digest


def parse_robot_list(robots_json: str) -> str:
    """From the JSON-array response of GET /robots?q=name=build-pusher,
    print the id of the matching robot or empty string."""
    try:
        rows = json.loads(robots_json)
    except json.JSONDecodeError:
        return ""
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if row.get("name") in ("robot$build-pusher", "build-pusher"):
            return str(row.get("id", ""))
    return ""


def robot_state(robot_json: str) -> str:
    """Classify the permissions array of a GET /robots/{id} response.

    Returns one of: empty | missing-core | already-has-cache | needs-cache.
    """
    data = json.loads(robot_json)
    perms = data.get("permissions") or []
    namespaces = {p.get("namespace") for p in perms if isinstance(p, dict)}
    if not perms:
        return "empty"
    if not {"lolday", "detectors"}.issubset(namespaces):
        return "missing-core"
    if "detectors-cache" in namespaces:
        return "already-has-cache"
    return "needs-cache"


def add_cache_perm(robot_json: str) -> str:
    """Append a detectors-cache repository:push+pull permission to the
    given GET /robots/{id} body and return the PUT body (JSON string)."""
    data = json.loads(robot_json)
    data.setdefault("permissions", []).append(
        {
            "kind": "project",
            "namespace": "detectors-cache",
            "access": [
                {"resource": "repository", "action": "push"},
                {"resource": "repository", "action": "pull"},
            ],
        }
    )
    keep = ["name", "level", "duration", "description", "disable", "editable", "expires_at", "permissions"]
    body = {k: data[k] for k in keep if k in data}
    return json.dumps(body)


def redact_robot_response(robot_json: str) -> str:
    """Echo the robot response body with the `secret` field replaced
    by '<redacted>'. Used for log lines that must never carry the
    plaintext secret."""
    data = json.loads(robot_json)
    redacted = {k: ("<redacted>" if k == "secret" else v) for k, v in data.items()}
    return json.dumps(redacted)


# --- input validation -------------------------------------------------

def _is_safe_sha(s: str) -> bool:
    """Mirror of build-helpers.sh's regex guard: short-12 subtree SHA up
    to full 64-char sha256 hex."""
    if not 6 <= len(s) <= 64:
        return False
    return all(c in "0123456789abcdef" for c in s)


def _is_sha256_digest(s: str) -> bool:
    return s.startswith("sha256:") and len(s) == len("sha256:") + 64 and _is_safe_sha(s[7:])


# --- CLI dispatch -----------------------------------------------------

def _dispatch(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m scripts.lib.harbor_api <verb> [args...]", file=sys.stderr)
        return 2
    verb, *args = argv
    try:
        if verb == "creds-namespace":
            print(creds_namespace())
        elif verb == "decode-dockerconfig":
            cfg = sys.stdin.read() if not args else open(args[0], encoding="utf-8").read()
            print(decode_dockerconfig(cfg))
        elif verb == "build-dockerconfig":
            user, secret, host = args
            print(build_dockerconfig(user, secret, host))
        elif verb == "has-tag":
            name, sha = args
            return 0 if has_tag(name, sha) else 1
        elif verb == "get-digest":
            name, sha = args
            print(get_digest(name, sha))
        elif verb == "parse-robot-list":
            print(parse_robot_list(sys.stdin.read()))
        elif verb == "robot-state":
            print(robot_state(sys.stdin.read()))
        elif verb == "add-cache-perm":
            print(add_cache_perm(sys.stdin.read()))
        elif verb == "redact-robot-response":
            print(redact_robot_response(sys.stdin.read()))
        else:
            print(f"unknown verb: {verb}", file=sys.stderr)
            return 2
    except (RuntimeError, ValueError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    return _dispatch(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Commit**

```bash
git add scripts/lib/__init__.py scripts/lib/harbor_api.py
git commit -m "$(cat <<'EOF'
refactor(scripts): extract harbor_api.py from build-helpers.sh + recover-harbor.sh

Phase 4 D4.2 R6 part 1 of 6. Pulls the nine repeated Harbor v2 REST
helpers (creds-namespace, decode-dockerconfig, build-dockerconfig,
has-tag, get-digest, parse-robot-list, robot-state, add-cache-perm,
redact-robot-response) into a typed Python module callable via
`python3 -m scripts.lib.harbor_api <verb>`.

Shell-side swaps land in part 3 (Task 6); pytest unit coverage lands
in part 2 (Task 5). The module reuses the same SHA-shape regex guard
the inline scripts already enforce (M-harbor-sha-validate) and the
same dockerconfigjson dual-host registration (containerd + docker).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §9 R6 + §10 D4.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: pytest unit tests for `harbor_api.py` (D4.2 — part 2 of 6)

**Files:**

- Create: `scripts/tests/__init__.py`
- Create: `scripts/tests/lib/__init__.py`
- Create: `scripts/tests/lib/test_harbor_api.py`

- [ ] **Step 1: Create the package markers**

```bash
mkdir -p scripts/tests/lib
touch scripts/tests/__init__.py scripts/tests/lib/__init__.py
```

- [ ] **Step 2: Write the failing pytest module**

Open `scripts/tests/lib/test_harbor_api.py`:

```python
"""Phase 4 D4.2 R6 — unit tests for scripts/lib/harbor_api.py.

Runs from the backend uv environment (`cd backend && uv run pytest ../scripts/tests/lib/`).
Uses respx to mock the Harbor REST endpoints and monkeypatched
subprocess for the kubectl probe path.
"""

from __future__ import annotations

import base64
import json
import subprocess
from typing import Any

import httpx
import pytest
import respx

from scripts.lib import harbor_api


# ---------- _is_safe_sha / _is_sha256_digest --------------------------

@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("0123456789ab", True),  # 12-char subtree SHA
        ("f" * 64, True),  # full sha256
        ("abc", False),  # too short
        ("g" * 12, False),  # invalid hex
        ("", False),  # empty
        ("0123456789ab; rm -rf /", False),  # contamination
    ],
)
def test_is_safe_sha(value: str, ok: bool) -> None:
    assert harbor_api._is_safe_sha(value) is ok


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("sha256:" + "a" * 64, True),
        ("sha256:" + "a" * 63, False),
        ("sha512:" + "a" * 64, False),
        ("a" * 64, False),
    ],
)
def test_is_sha256_digest(value: str, ok: bool) -> None:
    assert harbor_api._is_sha256_digest(value) is ok


# ---------- decode_dockerconfig / build_dockerconfig ------------------

def test_build_dockerconfig_registers_both_hosts() -> None:
    encoded = harbor_api.build_dockerconfig(
        user="robot$build-pusher",
        secret="s3cret",
        host_alias="harbor.lolday.svc.cluster.local:80",
    )
    cfg = json.loads(base64.b64decode(encoded).decode())
    assert "harbor.lolday.svc:80" in cfg["auths"]
    assert "harbor.lolday.svc.cluster.local:80" in cfg["auths"]
    # Auth tuple decodes back to the right user:secret pair.
    enc = cfg["auths"]["harbor.lolday.svc:80"]["auth"]
    assert base64.b64decode(enc).decode() == "robot$build-pusher:s3cret"


def test_decode_dockerconfig_picks_svc_alias_by_default() -> None:
    encoded = harbor_api.build_dockerconfig("u", "p", "harbor.lolday.svc.cluster.local:80")
    cfg = base64.b64decode(encoded).decode()
    assert harbor_api.decode_dockerconfig(cfg) == "u:p"


def test_decode_dockerconfig_falls_back_to_single_entry() -> None:
    cfg = json.dumps({"auths": {"some-other-host:443": {"auth": base64.b64encode(b"u:p").decode()}}})
    assert harbor_api.decode_dockerconfig(cfg) == "u:p"


def test_decode_dockerconfig_raises_on_ambiguous_missing_default() -> None:
    cfg = json.dumps(
        {
            "auths": {
                "a.example:80": {"auth": "x"},
                "b.example:80": {"auth": "y"},
            }
        }
    )
    with pytest.raises(KeyError, match="cannot disambiguate"):
        harbor_api.decode_dockerconfig(cfg)


# ---------- parse_robot_list / robot_state / add_cache_perm -----------

def test_parse_robot_list_picks_matching_name() -> None:
    body = json.dumps(
        [
            {"id": 1, "name": "robot$other"},
            {"id": 42, "name": "robot$build-pusher"},
        ]
    )
    assert harbor_api.parse_robot_list(body) == "42"


def test_parse_robot_list_handles_legacy_unprefixed_name() -> None:
    body = json.dumps([{"id": 7, "name": "build-pusher"}])
    assert harbor_api.parse_robot_list(body) == "7"


def test_parse_robot_list_returns_empty_on_no_match() -> None:
    body = json.dumps([{"id": 1, "name": "robot$something"}])
    assert harbor_api.parse_robot_list(body) == ""


def test_parse_robot_list_returns_empty_on_bad_json() -> None:
    assert harbor_api.parse_robot_list("not-json") == ""


@pytest.mark.parametrize(
    ("perms", "expected"),
    [
        ([], "empty"),
        ([{"namespace": "lolday"}], "missing-core"),
        ([{"namespace": "lolday"}, {"namespace": "detectors"}], "needs-cache"),
        (
            [
                {"namespace": "lolday"},
                {"namespace": "detectors"},
                {"namespace": "detectors-cache"},
            ],
            "already-has-cache",
        ),
    ],
)
def test_robot_state(perms: list[dict[str, Any]], expected: str) -> None:
    body = json.dumps({"permissions": perms})
    assert harbor_api.robot_state(body) == expected


def test_add_cache_perm_appends_detectors_cache() -> None:
    body = json.dumps(
        {
            "name": "build-pusher",
            "level": "system",
            "duration": 90,
            "permissions": [
                {"kind": "project", "namespace": "lolday", "access": []},
                {"kind": "project", "namespace": "detectors", "access": []},
            ],
        }
    )
    out = json.loads(harbor_api.add_cache_perm(body))
    namespaces = {p["namespace"] for p in out["permissions"]}
    assert namespaces == {"lolday", "detectors", "detectors-cache"}
    # Immutable fields preserved.
    assert out["name"] == "build-pusher"
    assert out["level"] == "system"


def test_redact_robot_response_hides_secret() -> None:
    body = json.dumps({"id": 5, "name": "build-pusher", "secret": "super-secret"})
    redacted = json.loads(harbor_api.redact_robot_response(body))
    assert redacted["secret"] == "<redacted>"
    assert redacted["id"] == 5
    assert redacted["name"] == "build-pusher"


# ---------- has_tag / get_digest via respx ----------------------------

@pytest.fixture
def stub_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend `kubectl get secret` returns a valid dockerconfigjson
    so the auth-header builder works without a real cluster."""
    cfg = harbor_api.build_dockerconfig(
        "robot$build-pusher",
        "secret",
        "harbor.lolday.svc.cluster.local:80",
    )
    decoded = base64.b64decode(cfg).decode()

    def fake_get_secret(namespace: str, name: str) -> str | None:
        if namespace == "lolday" and name == "harbor-push-cred":
            return decoded
        return None

    monkeypatch.setattr(harbor_api, "_kubectl_get_secret", fake_get_secret)
    monkeypatch.delenv("HARBOR_CRED_NS", raising=False)


@respx.mock
def test_has_tag_returns_true_on_non_empty_artifact_list(stub_creds: None) -> None:
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(200, json=[{"id": 1}])
    assert harbor_api.has_tag("build-helper", "0123456789ab") is True


@respx.mock
def test_has_tag_returns_false_on_empty_list(stub_creds: None) -> None:
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(200, json=[])
    assert harbor_api.has_tag("build-helper", "0123456789ab") is False


@respx.mock
def test_has_tag_returns_false_on_404(stub_creds: None) -> None:
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(404)
    assert harbor_api.has_tag("build-helper", "0123456789ab") is False


def test_has_tag_refuses_unsafe_sha() -> None:
    with pytest.raises(ValueError, match="non-SHA"):
        harbor_api.has_tag("build-helper", "; rm -rf /")


@respx.mock
def test_get_digest_returns_pinned_digest(stub_creds: None) -> None:
    digest = "sha256:" + "a" * 64
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(200, json=[{"digest": digest}])
    assert harbor_api.get_digest("build-helper", "0123456789ab") == digest


@respx.mock
def test_get_digest_raises_on_empty_list(stub_creds: None) -> None:
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(200, json=[])
    with pytest.raises(RuntimeError, match="empty artifact list"):
        harbor_api.get_digest("build-helper", "0123456789ab")


@respx.mock
def test_get_digest_raises_on_malformed_digest(stub_creds: None) -> None:
    respx.get(
        "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/build-helper/artifacts"
    ).respond(200, json=[{"digest": "sha512:" + "a" * 128}])
    with pytest.raises(RuntimeError, match="unexpected digest"):
        harbor_api.get_digest("build-helper", "0123456789ab")


# ---------- creds_namespace -------------------------------------------

def test_creds_namespace_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARBOR_CRED_NS", "custom-ns")
    assert harbor_api.creds_namespace() == "custom-ns"


def test_creds_namespace_probes_lolday_then_lolday_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARBOR_CRED_NS", raising=False)
    calls: list[tuple[str, str]] = []

    def fake_get_secret(namespace: str, name: str) -> str | None:
        calls.append((namespace, name))
        return "{}" if namespace == "lolday-jobs" else None

    monkeypatch.setattr(harbor_api, "_kubectl_get_secret", fake_get_secret)
    assert harbor_api.creds_namespace() == "lolday-jobs"
    assert calls == [("lolday", "harbor-push-cred"), ("lolday-jobs", "harbor-push-cred")]


def test_creds_namespace_raises_when_secret_missing_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARBOR_CRED_NS", raising=False)
    monkeypatch.setattr(harbor_api, "_kubectl_get_secret", lambda ns, n: None)
    with pytest.raises(RuntimeError, match="not found in any of"):
        harbor_api.creds_namespace()
```

- [ ] **Step 3: Run the suite from the backend env**

```bash
cd backend
uv run pytest ../scripts/tests/lib/test_harbor_api.py -v
```

Expected: all tests pass (around 26 cases).

- [ ] **Step 4: Commit**

```bash
git add scripts/tests/__init__.py scripts/tests/lib/__init__.py scripts/tests/lib/test_harbor_api.py
git commit -m "$(cat <<'EOF'
test(scripts): pytest unit for scripts/lib/harbor_api.py

Phase 4 D4.2 R6 part 2 of 6. Tests every public function: SHA shape
guards, dockerconfigjson encode/decode (both alias paths +
ambiguous-missing-default failure), robot-list parsing
(matching/empty/legacy/malformed), robot_state classification matrix
(empty/missing-core/needs-cache/already-has-cache), add_cache_perm
permission append + immutable-field preservation, redact_robot_response
secret-field masking, has_tag/get_digest happy paths + 404 +
empty-list + malformed-digest errors via respx, and creds_namespace
env-override + probe order + raise-when-missing.

26 cases. respx (assert_all_called default) catches any new
unmocked HTTP egress.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §9 R6 + §10 D4.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Refactor `build-helpers.sh` + `recover-harbor.sh` to call `harbor_api.py` (D4.2 — part 3 of 6)

**Files:**

- Modify: `scripts/build-helpers.sh` (replace inline `python3` heredocs that handle Harbor REST / dockerconfig parsing)
- Modify: `scripts/recover-harbor.sh` (replace inline `python3` heredocs that handle robot-list parsing, robot-state classification, add-cache-perm, dockerconfig builder, redact-robot-response)

- [ ] **Step 1: Replace `_harbor_creds_ns` in `build-helpers.sh`**

In `scripts/build-helpers.sh`, locate `_harbor_creds_ns()` (around line 97) and replace its body so it shells out to the Python module:

```bash
_harbor_creds_ns() {
  if [ -n "${HARBOR_CRED_NS:-}" ]; then
    echo "$HARBOR_CRED_NS"
    return 0
  fi
  local ns
  ns="$(PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.harbor_api creds-namespace 2>/dev/null)" || return 1
  HARBOR_CRED_NS="$ns"
  echo "$ns"
}
```

- [ ] **Step 2: Replace the dockerconfig auth-decode inside `harbor_login()`**

In `scripts/build-helpers.sh`, replace the embedded `python3 -c '...' <<<"$cfg"` block inside `harbor_login()`:

```bash
harbor_login() {
  local cred_ns
  if ! cred_ns="$(_harbor_creds_ns)"; then
    echo "ERROR: K8s Secret harbor-push-cred not found in lolday or lolday-jobs." >&2
    echo "       Run 'bash scripts/recover-harbor.sh' first to bootstrap" >&2
    echo "       Harbor projects + the robot account." >&2
    return 1
  fi
  local cfg auth user secret
  cfg="$(kubectl -n "$cred_ns" get secret harbor-push-cred \
           -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d)"
  auth="$(PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.harbor_api decode-dockerconfig <<<"$cfg")"
  user="${auth%%:*}"
  secret="${auth#*:}"
  echo "$secret" | \
    docker login "$HARBOR_HOST_PUSH" -u "$user" --password-stdin >/dev/null
}
```

- [ ] **Step 3: Replace `harbor_has_tag` and `harbor_get_digest` to delegate**

In `scripts/build-helpers.sh`, replace both function bodies:

```bash
harbor_has_tag() {
  local name=$1 sha=$2
  HARBOR_CRED_NS="${HARBOR_CRED_NS:-$(_harbor_creds_ns)}" \
    PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.harbor_api has-tag "$name" "$sha"
}

harbor_get_digest() {
  local name=$1 sha=$2
  HARBOR_CRED_NS="${HARBOR_CRED_NS:-$(_harbor_creds_ns)}" \
    PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.harbor_api get-digest "$name" "$sha"
}
```

- [ ] **Step 4: Replace embedded `python3 -c ...` blocks in `recover-harbor.sh`**

In `scripts/recover-harbor.sh`, replace:

- The `EXISTING_ID=$(echo "$LIST_JSON" | python3 -c '...')` block (around line 61) with:
  ```bash
  EXISTING_ID=$(PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.harbor_api parse-robot-list <<<"$LIST_JSON")
  ```
- The `ROBOT_STATE=$(echo "$CURRENT" | python3 -c '...')` block (around line 119) with:
  ```bash
  ROBOT_STATE=$(PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.harbor_api robot-state <<<"$CURRENT")
  ```
- The `NEW_BODY=$(echo "$CURRENT" | python3 -c '...')` block (around line 147) with:
  ```bash
  NEW_BODY=$(PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.harbor_api add-cache-perm <<<"$CURRENT")
  ```
- The redact block (around line 185) with:
  ```bash
  PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.harbor_api redact-robot-response <<<"$ROBOT_JSON" | sed 's/^/  response: /' >&2
  ```
- The `DOCKER_CFG_B64` builder block (around line 231) with:
  ```bash
  DOCKER_CFG_B64=$(PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.harbor_api build-dockerconfig "$ROBOT_NAME" "$ROBOT_SECRET" "$HARBOR_HOST")
  ```

The `POST_STATE=$(curl ... | python3 -c '...')` re-verification block (around line 165) can be replaced with two calls:

```bash
POST_STATE_BODY=$(curl -sf -u "$adm" "$api/robots/$EXISTING_ID")
POST_NS_STATE=$(PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.harbor_api robot-state <<<"$POST_STATE_BODY")
if [ "$POST_NS_STATE" != "already-has-cache" ]; then
  echo "  ERROR: PUT /robots/$EXISTING_ID returned 200 but state=$POST_NS_STATE — investigate Harbor logs" >&2
  exit 1
fi
```

- [ ] **Step 5: Verify the bash scripts still parse and basic flows work**

```bash
bash -n scripts/build-helpers.sh
bash -n scripts/recover-harbor.sh
bash scripts/build-helpers.sh --help
# (smoke-test --dry-run with the fixture from Task 3 if possible)
```

Expected: no parse errors, `--help` exit 0, fixture-driven `--dry-run` from Task 3's bats suite still passes (will be re-run in CI).

- [ ] **Step 6: Re-run the bats smoke (no regression)**

```bash
./tests/bats/bats-core/bin/bats tests/bats/build_helpers_smoke.bats || true   # only if bats locally available
```

If bats is not locally installed, skip — CI re-runs in Task 24.

- [ ] **Step 7: Commit**

```bash
git add scripts/build-helpers.sh scripts/recover-harbor.sh
git commit -m "$(cat <<'EOF'
refactor(scripts): swap inline python heredocs for harbor_api module calls

Phase 4 D4.2 R6 part 3 of 6. Replaces nine embedded
`python3 -<<'PY' ... PY` blocks (four in build-helpers.sh, five in
recover-harbor.sh) with `python3 -m scripts.lib.harbor_api <verb>`
invocations. Shell now treats Harbor REST + dockerconfig manipulation
as one external call per logical operation, and the bash scripts
shrink to orchestration only.

Re-verification path in recover-harbor.sh (post-PUT permissions check)
switches from a custom python check to two harbor_api invocations —
clearer error message when Harbor accepts the PUT but the state did
not actually transition.

Behaviour preserved: every shell-callable contract returns the same
exit codes and prints to the same stdout/stderr streams. Coverage
comes from the harbor_api pytest suite landed in Task 5.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §9 R6 + §10 D4.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Extract `scripts/lib/helpers_lock.py` (D4.2 — part 4 of 6)

**Files:**

- Create: `scripts/lib/helpers_lock.py`

- [ ] **Step 1: Write `scripts/lib/helpers_lock.py`**

Open `scripts/lib/helpers_lock.py`:

```python
"""charts/lolday/helpers.lock JSON read/write/drift-check helpers.

Phase 4 D4.2 R6 part 4 of 6. Extracts the lock-file logic out of
scripts/build-helpers.sh::write_lock and scripts/check-helpers-lock.sh.

Shell callers use:

    python3 -m scripts.lib.helpers_lock <verb> [args...]

verbs:
    read <path>                    — print JSON value of build_helper
                                     (one per line: build_helper,job_helper)
    write <path> <build> <job>     — atomically write a fresh lock JSON
    check-drift <path> [--repo R]  — compare lock entries against HEAD
                                     subtree SHAs; exit 0 clean,
                                     1 drift, 2 io-error

The lock format is the same one currently committed at
charts/lolday/helpers.lock:

    {
      "build_helper": "harbor.lolday.svc:80/lolday/build-helper:<sha12>@sha256:<64hex>",
      "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:<sha12>@sha256:<64hex>"
    }

The check-drift verb encodes H-21-img (every entry must carry the
@sha256:<hex> digest pin) and the existing tag-SHA-matches-HEAD
invariant.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
HELPER_KEYS = ("build_helper", "job_helper")


def read_lock(path: str | Path) -> dict[str, str]:
    """Load the JSON lock file. Raises FileNotFoundError if absent."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"helpers.lock at {path!r} is not a JSON object")
    return data


def write_lock(path: str | Path, build_ref: str, job_ref: str) -> None:
    """Atomically (tmp + rename) write a fresh lock file with the two
    helper refs, pretty-printed with sorted keys."""
    payload = {"build_helper": build_ref, "job_helper": job_ref}
    p = Path(path)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=p.parent, prefix=p.name + ".", suffix=".tmp", delete=False
    )
    try:
        json.dump(payload, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, p)
    except Exception:
        os.unlink(tmp.name)
        raise


def _git_subtree_sha(repo: Path, helper_name: str) -> str:
    """Return the 12-char tree SHA for charts/lolday/helpers/<helper> at HEAD."""
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--short=12", f"HEAD:charts/lolday/helpers/{helper_name}"],
        text=True,
    ).strip()


def check_drift(lock_path: str | Path, *, repo_root: str | Path) -> list[str]:
    """Compare lock entries against HEAD subtree SHAs. Returns a list
    of human-readable drift messages (empty list = clean)."""
    lock = read_lock(lock_path)
    drift: list[str] = []
    for key, ref in lock.items():
        helper = key.replace("_", "-")
        sha = _git_subtree_sha(Path(repo_root), helper)
        ref_no_digest = DIGEST_RE.sub("", ref)
        if not ref_no_digest.endswith(f":{sha}"):
            drift.append(f"  {helper}: lock={ref} HEAD=...:{sha}")
        if not DIGEST_RE.search(ref):
            drift.append(f"  {helper}: missing @sha256:<64-hex> digest pin: {ref}")
    return drift


# --- CLI dispatch -----------------------------------------------------

def _dispatch(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m scripts.lib.helpers_lock <verb> [args...]", file=sys.stderr)
        return 2
    verb, *args = argv
    try:
        if verb == "read":
            if not args:
                print("usage: read <path>", file=sys.stderr)
                return 2
            data = read_lock(args[0])
            for key in HELPER_KEYS:
                print(data.get(key, ""))
        elif verb == "write":
            if len(args) != 3:
                print("usage: write <path> <build_ref> <job_ref>", file=sys.stderr)
                return 2
            write_lock(args[0], args[1], args[2])
        elif verb == "check-drift":
            lock_path = args[0] if args else "charts/lolday/helpers.lock"
            repo_root = os.environ.get("LOLDAY_REPO_ROOT_OVERRIDE") or os.environ.get("REPO_ROOT") or "."
            # Allow --repo override after the positional path.
            if "--repo" in args:
                i = args.index("--repo")
                repo_root = args[i + 1]
            drift = check_drift(lock_path, repo_root=repo_root)
            if drift:
                print("ERROR: helpers.lock drift detected:", file=sys.stderr)
                for line in drift:
                    print(line, file=sys.stderr)
                print("Run 'bash scripts/build-helpers.sh' and commit the updated lock.", file=sys.stderr)
                return 1
        else:
            print(f"unknown verb: {verb}", file=sys.stderr)
            return 2
    except FileNotFoundError as e:
        print(f"ERROR: helpers.lock missing: {e}", file=sys.stderr)
        return 2
    except (ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    return _dispatch(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Commit**

```bash
git add scripts/lib/helpers_lock.py
git commit -m "$(cat <<'EOF'
refactor(scripts): extract helpers_lock.py from build-helpers.sh + check-helpers-lock.sh

Phase 4 D4.2 R6 part 4 of 6. Pulls three concerns into one Python
module: read_lock (JSON load with shape validation), write_lock
(atomic tmp+rename + fsync), and check_drift (tag-SHA matches HEAD
+ @sha256 digest pin present). Shell scripts will call this via
`python3 -m scripts.lib.helpers_lock <verb>` in part 6.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §9 R6 + §10 D4.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: pytest unit tests for `helpers_lock.py` (D4.2 — part 5 of 6)

**Files:**

- Create: `scripts/tests/lib/test_helpers_lock.py`

- [ ] **Step 1: Write the test module**

Open `scripts/tests/lib/test_helpers_lock.py`:

```python
"""Phase 4 D4.2 R6 — unit tests for scripts/lib/helpers_lock.py."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.lib import helpers_lock

# A syntactically valid digest used throughout.
DIGEST = "@sha256:" + "a" * 64


def _seed_git_repo(repo: Path) -> dict[str, str]:
    """Create a tiny git repo with the two helper subtrees and return
    their 12-char tree SHAs."""
    subprocess.check_call(["git", "-C", str(repo), "init", "-q"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.email", "t@t"])
    subprocess.check_call(["git", "-C", str(repo), "config", "user.name", "t"])
    for helper in ("build-helper", "job-helper"):
        sub = repo / "charts" / "lolday" / "helpers" / helper
        sub.mkdir(parents=True)
        (sub / "Dockerfile").write_text(f"FROM alpine\nLABEL helper={helper}\n")
    subprocess.check_call(["git", "-C", str(repo), "add", "-A"])
    subprocess.check_call(["git", "-C", str(repo), "commit", "-qm", "seed"])
    return {
        helper: subprocess.check_output(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--short=12",
                f"HEAD:charts/lolday/helpers/{helper}",
            ],
            text=True,
        ).strip()
        for helper in ("build-helper", "job-helper")
    }


# ---------- read_lock / write_lock round-trip -------------------------

def test_write_lock_creates_pretty_sorted_json(tmp_path: Path) -> None:
    lock = tmp_path / "helpers.lock"
    helpers_lock.write_lock(lock, "harbor.example/build-helper:abc" + DIGEST, "harbor.example/job-helper:def" + DIGEST)
    text = lock.read_text()
    assert text.endswith("\n")
    parsed = json.loads(text)
    assert parsed["build_helper"].endswith(DIGEST)
    assert parsed["job_helper"].endswith(DIGEST)
    # sort_keys=True invariant: build_helper appears before job_helper in raw text.
    assert text.index("build_helper") < text.index("job_helper")


def test_write_lock_is_atomic_no_intermediate_partial(tmp_path: Path) -> None:
    lock = tmp_path / "helpers.lock"
    helpers_lock.write_lock(lock, "a" + DIGEST, "b" + DIGEST)
    # The tmp file (suffixed .tmp) must not be left behind on success.
    leftovers = list(tmp_path.glob("helpers.lock.*.tmp"))
    assert leftovers == []


def test_read_lock_roundtrip(tmp_path: Path) -> None:
    lock = tmp_path / "helpers.lock"
    helpers_lock.write_lock(lock, "X" + DIGEST, "Y" + DIGEST)
    data = helpers_lock.read_lock(lock)
    assert data == {"build_helper": "X" + DIGEST, "job_helper": "Y" + DIGEST}


def test_read_lock_raises_on_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        helpers_lock.read_lock(tmp_path / "nope.lock")


def test_read_lock_rejects_non_object_payload(tmp_path: Path) -> None:
    lock = tmp_path / "helpers.lock"
    lock.write_text("[1, 2, 3]")
    with pytest.raises(ValueError, match="not a JSON object"):
        helpers_lock.read_lock(lock)


# ---------- check_drift -----------------------------------------------

def test_check_drift_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shas = _seed_git_repo(repo)
    lock = repo / "charts" / "lolday" / "helpers.lock"
    helpers_lock.write_lock(
        lock,
        f"harbor.lolday.svc:80/lolday/build-helper:{shas['build-helper']}{DIGEST}",
        f"harbor.lolday.svc:80/lolday/job-helper:{shas['job-helper']}{DIGEST}",
    )
    assert helpers_lock.check_drift(lock, repo_root=repo) == []


def test_check_drift_detects_sha_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_git_repo(repo)
    lock = repo / "charts" / "lolday" / "helpers.lock"
    helpers_lock.write_lock(
        lock,
        "harbor.lolday.svc:80/lolday/build-helper:000000000000" + DIGEST,
        "harbor.lolday.svc:80/lolday/job-helper:000000000000" + DIGEST,
    )
    drift = helpers_lock.check_drift(lock, repo_root=repo)
    assert len(drift) == 2
    assert any("build-helper" in line for line in drift)


def test_check_drift_detects_missing_digest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shas = _seed_git_repo(repo)
    lock = repo / "charts" / "lolday" / "helpers.lock"
    # Right SHAs but no @sha256 digest pin.
    helpers_lock.write_lock(
        lock,
        f"harbor.lolday.svc:80/lolday/build-helper:{shas['build-helper']}",
        f"harbor.lolday.svc:80/lolday/job-helper:{shas['job-helper']}",
    )
    drift = helpers_lock.check_drift(lock, repo_root=repo)
    assert all("missing @sha256" in line for line in drift)
    assert len(drift) == 2
```

- [ ] **Step 2: Run the suite**

```bash
cd backend
uv run pytest ../scripts/tests/lib/test_helpers_lock.py -v
```

Expected: 8 passing tests.

- [ ] **Step 3: Commit**

```bash
git add scripts/tests/lib/test_helpers_lock.py
git commit -m "$(cat <<'EOF'
test(scripts): pytest unit for scripts/lib/helpers_lock.py

Phase 4 D4.2 R6 part 5 of 6. Tests every public function on
helpers_lock.py: write_lock pretty + sorted + tmp-not-left, read_lock
round-trip + missing-file FileNotFoundError + non-object ValueError,
check_drift clean/sha-mismatch/missing-digest cases against a fresh
git fixture repo built in tmp_path.

8 cases.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §9 R6 + §10 D4.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Swap `build-helpers.sh::write_lock` + `check-helpers-lock.sh` to call `helpers_lock.py`; wire pytest into CI (D4.2 — part 6 of 6)

**Files:**

- Modify: `scripts/build-helpers.sh` (replace `write_lock` body and the two `existing_*` reads)
- Modify: `scripts/check-helpers-lock.sh` (replace inline `python3 -c '...'` drift block)
- Modify: `.github/workflows/backend-fast.yml` (add `pytest ../scripts/tests/lib/` step)

- [ ] **Step 1: Replace `write_lock()` body in `build-helpers.sh`**

In `scripts/build-helpers.sh`, around line 74:

```bash
write_lock() {
  local build_ref=$1 job_ref=$2
  PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.helpers_lock write \
    "$LOCK_FILE" "$build_ref" "$job_ref"
}
```

- [ ] **Step 2: Replace the `existing_build` / `existing_job` read in `main()`**

In `scripts/build-helpers.sh`, around line 490-502, replace the two inline `python3 -c` reads with a single helpers_lock invocation:

```bash
  local existing_build="" existing_job=""
  if [ -f "$LOCK_FILE" ]; then
    # helpers_lock.read returns one ref per line: build_helper then job_helper.
    { read -r existing_build; read -r existing_job; } < <(
      PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.helpers_lock read "$LOCK_FILE"
    )
  fi
```

- [ ] **Step 3: Replace inline `python3 - "$LOCK_FILE" <<'PY'` in `check-helpers-lock.sh`**

In `scripts/check-helpers-lock.sh`, lines 23-48, replace with:

```bash
PYTHONPATH="$REPO_ROOT" python3 -m scripts.lib.helpers_lock check-drift \
  "$LOCK_FILE" --repo "$REPO_ROOT"
exit $?
```

(Delete the entire `drift="$(...)"` block plus the subsequent `if [ -n "$drift" ]` guard — the exit code now flows directly from the module.)

- [ ] **Step 4: Verify the bash scripts still parse and the existing lock file passes drift check**

```bash
bash -n scripts/build-helpers.sh scripts/check-helpers-lock.sh
bash scripts/check-helpers-lock.sh
```

Expected: exit 0 (the committed `helpers.lock` is in sync with HEAD).

- [ ] **Step 5: Re-run bats smoke against the new scripts**

```bash
./tests/bats/bats-core/bin/bats tests/bats/ || true
```

If bats isn't locally installed, skip — CI re-runs in Task 24.

- [ ] **Step 6: Add the scripts-lib pytest step to `backend-fast.yml`**

In `.github/workflows/backend-fast.yml`, locate the existing pytest step (it runs `uv run pytest -m "not heavy"` or similar). Append a sibling step after it:

```yaml
- name: Run scripts/lib unit tests
  working-directory: backend
  run: |
    uv run pytest ../scripts/tests/lib/ -v --tb=short
```

(Exact location: after the existing pytest invocation, before any "Upload JUnit" step. If the upload step exists, move the new step _before_ the upload so its JUnit is included if it emits one — for now it does not.)

- [ ] **Step 7: Commit**

```bash
git add scripts/build-helpers.sh scripts/check-helpers-lock.sh .github/workflows/backend-fast.yml
git commit -m "$(cat <<'EOF'
refactor(scripts): swap write_lock + check-drift inlines for helpers_lock module

Phase 4 D4.2 R6 part 6 of 6. Three shell-side swaps:

- build-helpers.sh::write_lock body delegates to `python3 -m
  scripts.lib.helpers_lock write` (atomic tmp+rename moves out of bash
  into the python module).
- build-helpers.sh::main() reads existing lock entries via the `read`
  verb, one line each for build_helper + job_helper; replaces two
  separate `python3 -c '...' "$LOCK_FILE"` invocations.
- check-helpers-lock.sh inline drift block deleted; the script now
  just dispatches to `helpers_lock check-drift` and exits with its
  status.

backend-fast.yml gains one new step: `uv run pytest ../scripts/tests/lib/`
so harbor_api + helpers_lock tests run on every PR. No new workflow
file (re-uses the existing backend-fast.yml runner; pytest discovers
the new tree via the explicit path arg).

Re-runs CI: backend-fast (now also covers scripts/lib) + bats (smoke
on the modified shell scripts) + lint (pre-commit reformats; no
changes expected).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §9 R6 + §10 D4.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Add `mutmut` to dev deps (D4.3 — part 1 of 4)

**Files:**

- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock` (regenerated by `uv add`)

- [ ] **Step 1: Add the dev dep**

```bash
cd backend
uv add --dev "mutmut>=3,<4"
cd ..
```

This updates `pyproject.toml`'s `[dependency-groups].dev` table and regenerates `uv.lock`.

- [ ] **Step 2: Verify mutmut installs and the CLI runs**

```bash
cd backend
uv run mutmut --help | head -20
cd ..
```

Expected: usage banner mentioning `run`, `results`, etc.

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "$(cat <<'EOF'
chore(backend): add mutmut to dev deps (Phase 4 D4.3 setup)

Phase 4 D4.3 part 1 of 4. mutmut 3.x is the mainstream Python
mutation-testing framework (also: cosmic-ray, but mutmut is the
better-maintained one as of 2026-05). Config goes into a [tool.mutmut]
section of pyproject.toml in part 2; weekly cron runs in part 4.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 D4.3

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: `[tool.mutmut]` config in `backend/pyproject.toml` (D4.3 — part 2 of 4)

**Files:**

- Modify: `backend/pyproject.toml` (append the `[tool.mutmut]` block)

- [ ] **Step 1: Add the `[tool.mutmut]` block**

In `backend/pyproject.toml`, append (after the existing `[tool.pytest.ini_options]` block):

```toml
[tool.mutmut]
# Phase 4 D4.3 — weekly mutation tier. Targets the top-10 high-risk
# modules per spec §1.2 (ranked by 30-day churn × fan-in × postmortem
# involvement). Phase 4 exit gate: ≥60% killed per module; final
# target (Phase 4 spec §7.4): ≥80%.
paths_to_mutate = [
    "app/routers/jobs.py",
    "app/reconciler/jobs.py",
    "app/services/mlflow_client.py",
    "app/reconciler/fifo_scheduler.py",
    "app/services/build.py",
    "app/routers/experiments_proxy.py",
    "app/auth/cf_access.py",
    "app/models/job.py",
    "app/services/gpu_signal.py",
    "app/reconciler/build_finalize.py",
]
tests_dir = "tests/"
# mutmut runs pytest under the hood; reuse the markers config so heavy
# tests stay skipped during mutation runs (heavy = testcontainers, would
# take ~hours per module).
runner = "uv run pytest -m 'not heavy' -x --tb=no -q"
```

- [ ] **Step 2: Smoke-test a single-module mutmut run locally (optional, slow)**

```bash
cd backend
# only run if you have ~5 min — single module
uv run mutmut run --paths-to-mutate app/models/job.py || true
uv run mutmut results || true
cd ..
```

This is slow; CI is the canonical runner. Skip if local time is constrained.

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml
git commit -m "$(cat <<'EOF'
chore(backend): mutmut config pinning top-10 risk modules

Phase 4 D4.3 part 2 of 4. [tool.mutmut] in backend/pyproject.toml lists
the ten high-risk modules ranked in spec §1.2 (routers/jobs.py,
reconciler/jobs.py, services/mlflow_client.py, …). runner reuses
pytest with `-m 'not heavy'` so testcontainers tests don't run during
mutation (would inflate runtime from minutes per module to hours).

Mutation runner script + weekly cron land in parts 3 + 4 (Tasks 12-13).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §1.2 + §10 D4.3

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: `scripts/lib/mutation_report.py` + pytest unit (D4.3 — part 3 of 4)

**Files:**

- Create: `scripts/lib/mutation_report.py`
- Create: `scripts/tests/lib/test_mutation_report.py`

- [ ] **Step 1: Write the report generator**

Open `scripts/lib/mutation_report.py`:

```python
"""Render mutmut results as a Markdown report.

Phase 4 D4.3 part 3 of 4. Invoked by .github/workflows/mutation.yml
(part 4). Reads `mutmut results --json` output on stdin (or via
--input <file>) and writes a Markdown table to
docs/test-telemetry/mutation-<YYYY-MM-DD>.md.

mutmut's JSON shape (3.x):
{
  "module/path.py": {
    "killed": [<mutation-id>...],
    "survived": [...],
    "skipped": [...],
    "no_tests": [...],
    "suspicious": [...],
    "timeout": [...]
  },
  ...
}

The report aggregates per-module counts, computes killed-rate
(killed / (killed + survived + suspicious)), and flags any module
whose rate is below the Phase 4 exit gate (60%).
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

PHASE_4_KILL_THRESHOLD = 0.60
PHASE_4_TARGET = 0.80


def render(results: dict[str, dict[str, list]], *, today: datetime.date | None = None) -> str:
    """Format the mutmut results dict as a Markdown report string."""
    today = today or datetime.date.today()
    lines: list[str] = []
    lines.append(f"# Mutation testing report — {today.isoformat()}")
    lines.append("")
    lines.append("Phase 4 D4.3 — weekly cron output. Threshold for Phase 4 exit: ≥ 60%; target ≥ 80%.")
    lines.append("")
    lines.append("| Module | Killed | Survived | Suspicious | Skipped | No-tests | Timeout | Kill-rate | Flag |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")

    below: list[str] = []
    for module in sorted(results):
        bucket = results[module]
        killed = len(bucket.get("killed", []))
        survived = len(bucket.get("survived", []))
        suspicious = len(bucket.get("suspicious", []))
        skipped = len(bucket.get("skipped", []))
        no_tests = len(bucket.get("no_tests", []))
        timeout = len(bucket.get("timeout", []))
        denom = killed + survived + suspicious
        if denom == 0:
            kill_rate = float("nan")
            rate_str = "n/a"
            flag = "no mutants"
        else:
            kill_rate = killed / denom
            rate_str = f"{kill_rate:.0%}"
            flag = ""
            if kill_rate < PHASE_4_KILL_THRESHOLD:
                flag = "BELOW 60%"
                below.append(f"- `{module}` killed {kill_rate:.0%} ({killed}/{denom})")
            elif kill_rate < PHASE_4_TARGET:
                flag = "below 80%"
        lines.append(
            f"| `{module}` | {killed} | {survived} | {suspicious} | {skipped} | {no_tests} | {timeout} | {rate_str} | {flag} |"
        )

    lines.append("")
    if below:
        lines.append("## Action items (kill-rate < 60%)")
        lines.append("")
        lines.extend(below)
        lines.append("")
    else:
        lines.append("All targeted modules meet the Phase 4 exit gate (≥ 60%).")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mutation_report")
    parser.add_argument("--input", default="-", help="JSON input file (- for stdin)")
    parser.add_argument("--output", required=True, help="Markdown output path")
    args = parser.parse_args(argv)

    if args.input == "-":
        results = json.load(sys.stdin)
    else:
        with open(args.input, encoding="utf-8") as f:
            results = json.load(f)

    md = render(results)
    Path(args.output).write_text(md, encoding="utf-8")
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the test**

Open `scripts/tests/lib/test_mutation_report.py`:

```python
"""Phase 4 D4.3 — unit tests for scripts/lib/mutation_report.py."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from scripts.lib import mutation_report


def test_render_includes_header_and_table() -> None:
    md = mutation_report.render({}, today=datetime.date(2026, 5, 16))
    assert "# Mutation testing report — 2026-05-16" in md
    assert "| Module |" in md


def test_render_flags_module_below_60_percent() -> None:
    results = {
        "app/routers/jobs.py": {
            "killed": list(range(5)),  # 5
            "survived": list(range(10)),  # 10 → 5/15 = 33% < 60%
        }
    }
    md = mutation_report.render(results, today=datetime.date(2026, 5, 16))
    assert "BELOW 60%" in md
    assert "Action items" in md
    assert "33%" in md


def test_render_marks_below_target_but_above_gate() -> None:
    # 7/10 = 70% — above 60%, below 80%.
    results = {
        "app/services/build.py": {
            "killed": list(range(7)),
            "survived": list(range(3)),
        }
    }
    md = mutation_report.render(results, today=datetime.date(2026, 5, 16))
    assert "below 80%" in md
    assert "BELOW 60%" not in md


def test_render_reports_clean_when_all_pass_gate() -> None:
    results = {
        "app/services/build.py": {
            "killed": list(range(9)),
            "survived": [1],
        }
    }
    md = mutation_report.render(results, today=datetime.date(2026, 5, 16))
    assert "meet the Phase 4 exit gate" in md
    assert "Action items" not in md


def test_render_handles_module_with_no_mutants() -> None:
    md = mutation_report.render(
        {"app/models/job.py": {"killed": [], "survived": [], "suspicious": []}},
        today=datetime.date(2026, 5, 16),
    )
    assert "no mutants" in md


def test_main_writes_markdown_file(tmp_path: Path) -> None:
    payload = {"app/models/job.py": {"killed": [1, 2, 3], "survived": []}}
    in_file = tmp_path / "results.json"
    in_file.write_text(json.dumps(payload))
    out_file = tmp_path / "out.md"
    rc = mutation_report.main(["--input", str(in_file), "--output", str(out_file)])
    assert rc == 0
    text = out_file.read_text()
    assert "100%" in text
    assert "app/models/job.py" in text
```

- [ ] **Step 3: Run the suite**

```bash
cd backend
uv run pytest ../scripts/tests/lib/test_mutation_report.py -v
cd ..
```

Expected: 6 passing tests.

- [ ] **Step 4: Commit**

```bash
git add scripts/lib/mutation_report.py scripts/tests/lib/test_mutation_report.py
git commit -m "$(cat <<'EOF'
test(scripts): mutation_report.py + unit (Phase 4 D4.3 part 3)

mutation_report.py renders mutmut --json output as a per-module
Markdown table with killed / survived / suspicious / skipped / no-tests
/ timeout counts and a kill-rate column. Flags modules below the
Phase 4 exit gate (60%) with `BELOW 60%`; below-target (60% ≤ rate <
80%) with `below 80%`; clean modules with empty cell. Generates an
"Action items" section listing every below-gate module.

Test suite covers: header + table render, below-60% flag, below-80%
marker, clean module reporting, no-mutants handling, end-to-end main()
with file IO via tmp_path.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 D4.3

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: `mutation.yml` weekly cron workflow (D4.3 — part 4 of 4)

**Files:**

- Create: `.github/workflows/mutation.yml`

- [ ] **Step 1: Write the workflow**

Open `.github/workflows/mutation.yml`:

```yaml
name: mutation

on:
  schedule:
    - cron: "0 6 * * 1" # 14:00 Asia/Taipei every Monday
  workflow_dispatch:

permissions:
  contents: write # commit the report back to main
  issues: write # open a tracking issue when any module is below the gate

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false

jobs:
  mutmut:
    name: mutmut weekly
    runs-on: ubuntu-24.04
    timeout-minutes: 90
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2

      - uses: ./.github/actions/setup-uv
        with:
          working-directory: backend

      - name: Run mutmut
        working-directory: backend
        run: |
          uv run mutmut run || true   # continues even if mutants survive
          uv run mutmut results --json > ../mutmut-results.json

      - name: Render Markdown report
        id: render
        run: |
          DATE=$(date -u +%Y-%m-%d)
          mkdir -p docs/test-telemetry
          OUT="docs/test-telemetry/mutation-${DATE}.md"
          cat mutmut-results.json | python3 -m scripts.lib.mutation_report --input - --output "$OUT"
          echo "report=$OUT" >> "$GITHUB_OUTPUT"
          echo "date=$DATE" >> "$GITHUB_OUTPUT"

      - name: Commit report to main
        uses: stefanzweifel/git-auto-commit-action@b863ae1933cb653a53c021fe36dbb774e1fb9403 # v5.2.0
        with:
          commit_message: "docs(test-telemetry): mutation report ${{ steps.render.outputs.date }}"
          file_pattern: docs/test-telemetry/mutation-*.md
          branch: main

      - name: Open tracking issue on below-gate modules
        if: success()
        env:
          GH_TOKEN: ${{ github.token }}
          REPORT_PATH: ${{ steps.render.outputs.report }}
        run: |
          if grep -q "BELOW 60%" "$REPORT_PATH"; then
            DATE="${{ steps.render.outputs.date }}"
            BODY=$(awk '/## Action items/,0' "$REPORT_PATH")
            gh issue create \
              -R bolin8017/lolday \
              -t "Mutation kill-rate below Phase 4 exit gate (${DATE})" \
              -l "tech-debt-tests" \
              -b "Auto-generated by .github/workflows/mutation.yml on ${DATE}. See ${REPORT_PATH} for the full table.\n\n${BODY}"
          else
            echo "All modules ≥60% — no issue opened."
          fi
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/mutation.yml
git commit -m "$(cat <<'EOF'
ci(mutation): weekly mutmut cron + docs/test-telemetry/ report writer

Phase 4 D4.3 part 4 of 4. Fires Monday 06:00 UTC (14:00 Asia/Taipei),
runs mutmut against the ten high-risk modules listed in
backend/pyproject.toml [tool.mutmut], renders the results JSON via
scripts/lib/mutation_report.py, commits the new
docs/test-telemetry/mutation-YYYY-MM-DD.md back to main via
git-auto-commit-action, and opens a tracking issue (label
tech-debt-tests) when any module's kill-rate is below 60%.

permissions: contents:write (commit report) + issues:write (track-
bracket-creation). timeout-minutes: 90 — 10 modules × ~5 min mutants
each = ~50 min upper bound; 90 leaves headroom for cold cache.
`uv run mutmut run || true` lets the workflow continue when survived
mutants are present (the whole point of this gate is to report them,
not block).

mutation.yml is informational; never blocks PRs. The label routes
the auto-issue into the existing tech-debt triage queue without
spamming a generic "flaky" channel.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 D4.3

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: `scripts/lib/test_telemetry.py` JUnit aggregator (D4.4 — part 1 of 4)

**Files:**

- Create: `scripts/lib/test_telemetry.py`

- [ ] **Step 1: Write the aggregator**

Open `scripts/lib/test_telemetry.py`:

```python
"""Aggregate JUnit XML test reports into a Markdown dashboard.

Phase 4 D4.4 part 1 of 4. Invoked from .github/workflows/test-telemetry.yml
(part 3) on a weekly cron. Walks a directory of JUnit XML files
(downloaded from the last 7 days of workflow artifacts via
actions/github-script — same pattern as flaky-tracker.yml),
aggregates per-test runtime + pass/fail stats, and rewrites
docs/test-telemetry/dashboard.md with five sections:

1. Per-test 30-day P50 / P95 / P99 duration (top 30 slow).
2. Per-test 7-day failure rate (anything > 0%).
3. Flaky candidates (failure rate > 1%) — pointer to flaky-tracker.
4. Slow tests (P99 > 30s).
5. Run count and total wall-clock per workflow.

A small Discord-friendly 5-line summary is also produced for the
Spidey Warnings channel.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TypedDict

FLAKY_THRESHOLD = 0.01  # 1%
SLOW_P99_SECONDS = 30.0


class TestStat(TypedDict):
    runs: int
    fails: int
    durations: list[float]


def parse_junit_dir(artifact_dir: Path) -> dict[str, TestStat]:
    """Walk artifact_dir for junit*.xml; aggregate per-test stats."""
    stats: dict[str, TestStat] = collections.defaultdict(
        lambda: {"runs": 0, "fails": 0, "durations": []}
    )
    for xml in artifact_dir.rglob("junit*.xml"):
        try:
            tree = ET.parse(xml)  # local artifact, not network input
        except ET.ParseError as e:
            print(f"[warn] skipping malformed XML {xml}: {e}", file=sys.stderr)
            continue
        for case in tree.iterfind(".//testcase"):
            classname = case.get("classname", "")
            name = case.get("name", "")
            tid = f"{classname}::{name}"
            stats[tid]["runs"] += 1
            if case.find("failure") is not None or case.find("error") is not None:
                stats[tid]["fails"] += 1
            try:
                stats[tid]["durations"].append(float(case.get("time", "0") or 0.0))
            except ValueError:
                pass
    return dict(stats)


def _percentile(values: list[float], pct: float) -> float:
    """Return the `pct` percentile of `values` (0 ≤ pct ≤ 100)."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    k = (len(s) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def render_dashboard(
    stats: dict[str, TestStat],
    *,
    today: datetime.date | None = None,
) -> str:
    today = today or datetime.date.today()
    lines: list[str] = []
    lines.append("# Test execution telemetry dashboard")
    lines.append("")
    lines.append(f"_Last updated: {today.isoformat()} (regenerated weekly by `.github/workflows/test-telemetry.yml`)._")
    lines.append("")
    lines.append(f"Total tests tracked: **{len(stats)}**.")
    lines.append("")

    # Slow ranking (top 30 by P99)
    slow_rows: list[tuple[str, float, float, float, int]] = []
    flaky_rows: list[tuple[str, int, int, float]] = []
    above_p99_threshold: list[tuple[str, float]] = []
    for tid, s in stats.items():
        if s["durations"]:
            p50 = statistics.median(s["durations"])
            p95 = _percentile(s["durations"], 95)
            p99 = _percentile(s["durations"], 99)
            slow_rows.append((tid, p50, p95, p99, s["runs"]))
            if p99 > SLOW_P99_SECONDS:
                above_p99_threshold.append((tid, p99))
        if s["runs"] > 0:
            rate = s["fails"] / s["runs"]
            if rate > FLAKY_THRESHOLD:
                flaky_rows.append((tid, s["fails"], s["runs"], rate))

    lines.append("## Slow tests (top 30 by P99)")
    lines.append("")
    lines.append("| Test | P50 (s) | P95 (s) | P99 (s) | Runs |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    slow_rows.sort(key=lambda r: -r[3])
    for tid, p50, p95, p99, runs in slow_rows[:30]:
        lines.append(f"| `{tid}` | {p50:.2f} | {p95:.2f} | {p99:.2f} | {runs} |")
    lines.append("")

    lines.append(f"## Flaky candidates (failure rate > {FLAKY_THRESHOLD:.0%})")
    lines.append("")
    if not flaky_rows:
        lines.append("None this week. ✓")
    else:
        lines.append("| Test | Fails | Runs | Rate |")
        lines.append("| --- | ---: | ---: | ---: |")
        flaky_rows.sort(key=lambda r: -r[3])
        for tid, fails, runs, rate in flaky_rows:
            lines.append(f"| `{tid}` | {fails} | {runs} | {rate:.1%} |")
        lines.append("")
        lines.append(
            "These tests should already have a `flaky-tracker.yml`-opened "
            "issue. If not, file one and apply `@pytest.mark.flaky_tracked` "
            "per `.claude/rules/testing.md`."
        )
    lines.append("")

    lines.append(f"## Slow-tier warnings (P99 > {SLOW_P99_SECONDS:.0f}s)")
    lines.append("")
    if not above_p99_threshold:
        lines.append("None this week. ✓")
    else:
        above_p99_threshold.sort(key=lambda r: -r[1])
        for tid, p99 in above_p99_threshold:
            lines.append(f"- `{tid}` — P99 = {p99:.1f}s")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_discord_summary(stats: dict[str, TestStat]) -> str:
    """Five-line summary for the Spidey Warnings channel."""
    total_tests = len(stats)
    total_runs = sum(s["runs"] for s in stats.values())
    total_fails = sum(s["fails"] for s in stats.values())
    flaky = sum(
        1 for s in stats.values() if s["runs"] > 0 and s["fails"] / s["runs"] > FLAKY_THRESHOLD
    )
    slow = sum(
        1 for s in stats.values() if s["durations"] and _percentile(s["durations"], 99) > SLOW_P99_SECONDS
    )
    overall_rate = (total_fails / total_runs) if total_runs else 0.0
    return (
        "**Test telemetry — weekly summary**\n"
        f"Total tests tracked: {total_tests}\n"
        f"Total runs: {total_runs} (fails: {total_fails}, overall rate: {overall_rate:.2%})\n"
        f"Flaky candidates (>1% failure): {flaky}\n"
        f"Slow tests (P99 > {SLOW_P99_SECONDS:.0f}s): {slow}\n"
        f"Dashboard: docs/test-telemetry/dashboard.md"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="test_telemetry")
    parser.add_argument("artifact_dir", help="directory of JUnit XML files")
    parser.add_argument("--dashboard-out", required=True, help="dashboard.md path")
    parser.add_argument("--summary-out", default=None, help="discord summary path (optional)")
    args = parser.parse_args(argv)

    stats = parse_junit_dir(Path(args.artifact_dir))
    Path(args.dashboard_out).write_text(render_dashboard(stats), encoding="utf-8")
    if args.summary_out:
        Path(args.summary_out).write_text(render_discord_summary(stats), encoding="utf-8")
    print(f"aggregated {len(stats)} tests from {args.artifact_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Commit**

```bash
git add scripts/lib/test_telemetry.py
git commit -m "$(cat <<'EOF'
feat(scripts): test_telemetry.py — JUnit aggregator + dashboard renderer

Phase 4 D4.4 part 1 of 4. Parses junit*.xml under a directory tree,
aggregates per-test runs + fails + durations, then emits two artifacts:

- dashboard.md: slow-test ranking by P99 (top 30), flaky candidates
  (failure rate > 1%), slow-tier warnings (P99 > 30s).
- discord summary (optional): 5-line Spidey Warnings post with total
  tests, total runs, flaky count, slow count.

Percentile math is in-module (no numpy dep). Skips malformed XML with
a warning, so a single corrupt artifact does not break the run.

Tests land in Task 15; weekly cron in Task 16.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §6.7 + §10 D4.4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: pytest unit for `test_telemetry.py` (D4.4 — part 2 of 4)

**Files:**

- Create: `scripts/tests/lib/test_test_telemetry.py`

- [ ] **Step 1: Write the test module**

Open `scripts/tests/lib/test_test_telemetry.py`:

```python
"""Phase 4 D4.4 — unit tests for scripts/lib/test_telemetry.py."""

from __future__ import annotations

import datetime
import textwrap
from pathlib import Path

import pytest

from scripts.lib import test_telemetry


def _write_junit(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).strip(), encoding="utf-8")


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    art = tmp_path / "artifacts"
    art.mkdir()
    # Run 1: two passing, one slow.
    _write_junit(
        art / "junit-1.xml",
        """
        <testsuites>
          <testsuite name="suite1">
            <testcase classname="m.x" name="fast_a" time="0.01"/>
            <testcase classname="m.x" name="fast_b" time="0.05"/>
            <testcase classname="m.x" name="slow_c" time="42.5"/>
          </testsuite>
        </testsuites>
        """,
    )
    # Run 2: fast_a now fails; slow_c finishes faster this time.
    _write_junit(
        art / "junit-2.xml",
        """
        <testsuites>
          <testsuite name="suite1">
            <testcase classname="m.x" name="fast_a" time="0.02">
              <failure message="boom">Trace</failure>
            </testcase>
            <testcase classname="m.x" name="fast_b" time="0.04"/>
            <testcase classname="m.x" name="slow_c" time="25.0"/>
          </testsuite>
        </testsuites>
        """,
    )
    return art


def test_parse_junit_dir_aggregates_stats(fixture_dir: Path) -> None:
    stats = test_telemetry.parse_junit_dir(fixture_dir)
    assert set(stats.keys()) == {"m.x::fast_a", "m.x::fast_b", "m.x::slow_c"}
    assert stats["m.x::fast_a"]["runs"] == 2
    assert stats["m.x::fast_a"]["fails"] == 1
    assert stats["m.x::fast_a"]["durations"] == [0.01, 0.02]


def test_render_dashboard_flags_flaky_and_slow(fixture_dir: Path) -> None:
    stats = test_telemetry.parse_junit_dir(fixture_dir)
    md = test_telemetry.render_dashboard(stats, today=datetime.date(2026, 5, 16))
    # Slow row present in slow table.
    assert "`m.x::slow_c`" in md
    # Flaky candidate fast_a (50% fails) appears.
    assert "50.0%" in md
    # Slow-tier warning section lists slow_c.
    assert "P99 = 42.5s" in md or "P99 = 42.0s" in md  # percentile interp may round to ~42.x


def test_render_discord_summary_is_five_lines(fixture_dir: Path) -> None:
    stats = test_telemetry.parse_junit_dir(fixture_dir)
    summary = test_telemetry.render_discord_summary(stats)
    body_lines = summary.split("\n")
    # Header + 5 content lines = 6 lines.
    assert len(body_lines) == 6
    assert "weekly summary" in body_lines[0]


def test_parse_junit_dir_skips_malformed_xml(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "junit-broken.xml").write_text("<not-xml>")
    (art / "junit-ok.xml").write_text(
        '<testsuites><testsuite name="s"><testcase classname="x" name="y" time="1"/></testsuite></testsuites>'
    )
    stats = test_telemetry.parse_junit_dir(art)
    assert "x::y" in stats


def test_render_dashboard_empty_clean(tmp_path: Path) -> None:
    md = test_telemetry.render_dashboard({}, today=datetime.date(2026, 5, 16))
    assert "None this week" in md
    assert "Total tests tracked: **0**" in md


def test_main_writes_both_outputs(fixture_dir: Path, tmp_path: Path) -> None:
    dash = tmp_path / "dash.md"
    summary = tmp_path / "summary.txt"
    rc = test_telemetry.main(
        [str(fixture_dir), "--dashboard-out", str(dash), "--summary-out", str(summary)]
    )
    assert rc == 0
    assert dash.exists()
    assert summary.exists()
    assert "weekly summary" in summary.read_text()
```

- [ ] **Step 2: Run the suite**

```bash
cd backend
uv run pytest ../scripts/tests/lib/test_test_telemetry.py -v
cd ..
```

Expected: 6 passing tests.

- [ ] **Step 3: Commit**

```bash
git add scripts/tests/lib/test_test_telemetry.py
git commit -m "$(cat <<'EOF'
test(scripts): unit for test_telemetry.py

Phase 4 D4.4 part 2 of 4. Fixture builds two JUnit XML files with
overlapping testcases; tests assert the aggregator collapses runs +
fails + durations correctly, the dashboard markdown flags the flaky
case (50% failure) and the slow case (P99 > 30s), the Discord
summary is exactly 5 content lines (+ header), malformed XML is
skipped with a warning rather than crashing, the empty case renders
"None this week", and main() writes both output files.

6 cases.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §6.7 + §10 D4.4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: `test-telemetry.yml` weekly cron + Discord post (D4.4 — part 3 of 4)

**Files:**

- Create: `.github/workflows/test-telemetry.yml`

- [ ] **Step 1: Write the workflow**

Open `.github/workflows/test-telemetry.yml`:

```yaml
name: test-telemetry

on:
  schedule:
    - cron: "30 6 * * 1" # 14:30 Asia/Taipei every Monday (30 min after mutation.yml)
  workflow_dispatch:

permissions:
  contents: write # commit dashboard back to main
  actions: read # download artifacts

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false

jobs:
  aggregate:
    name: aggregate JUnit + post Discord summary
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2

      - name: Download last 7 days of JUnit artifacts
        uses: actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3 # v9.0.0
        with:
          script: |
            const since = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
            const { data: { artifacts } } = await github.rest.actions.listArtifactsForRepo({
              owner: 'bolin8017',
              repo: 'lolday',
              per_page: 100,
            });
            const recent = artifacts.filter(a =>
              a.name.startsWith('junit-') && a.created_at > since
            );
            console.log(`Found ${recent.length} recent JUnit artifacts`);
            const fs = require('fs');
            fs.mkdirSync('artifacts', { recursive: true });
            for (const art of recent) {
              const { data } = await github.rest.actions.downloadArtifact({
                owner: 'bolin8017',
                repo: 'lolday',
                artifact_id: art.id,
                archive_format: 'zip',
              });
              fs.writeFileSync(`artifacts/${art.id}.zip`, Buffer.from(data));
            }
            const { execSync } = require('child_process');
            const dir = 'artifacts';
            for (const entry of fs.readdirSync(dir)) {
              if (entry.endsWith('.zip')) {
                const sub = `${dir}/${entry.replace('.zip', '')}`;
                fs.mkdirSync(sub, { recursive: true });
                execSync(`unzip -q ${dir}/${entry} -d ${sub}`);
              }
            }

      - name: Run aggregator
        run: |
          python3 -m scripts.lib.test_telemetry artifacts/ \
            --dashboard-out docs/test-telemetry/dashboard.md \
            --summary-out /tmp/discord-summary.txt

      - name: Commit dashboard
        uses: stefanzweifel/git-auto-commit-action@b863ae1933cb653a53c021fe36dbb774e1fb9403 # v5.2.0
        with:
          commit_message: "docs(test-telemetry): weekly dashboard refresh"
          file_pattern: docs/test-telemetry/dashboard.md
          branch: main

      - name: Post summary to Spidey Warnings
        if: env.DISCORD_WEBHOOK_URL_WARNING != ''
        env:
          DISCORD_WEBHOOK_URL_WARNING: ${{ secrets.DISCORD_WEBHOOK_URL_WARNING }}
        run: |
          SUMMARY=$(cat /tmp/discord-summary.txt)
          # Embed the summary as a Discord-flavoured payload.
          jq -nc \
            --arg content "$SUMMARY" \
            '{content: $content}' \
            | curl -sS -X POST -H 'Content-Type: application/json' \
                --data @- "$DISCORD_WEBHOOK_URL_WARNING"
```

- [ ] **Step 2: Verify the workflow file passes yamllint / actionlint shape (lint already runs in CI; just smoke-test locally)**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test-telemetry.yml'))"
```

Expected: no exceptions.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/test-telemetry.yml
git commit -m "$(cat <<'EOF'
ci(test-telemetry): weekly JUnit aggregation + Spidey Warnings post

Phase 4 D4.4 part 3 of 4. Mirrors flaky-tracker.yml's
actions/github-script artifact-download pattern but ingests JUnit
XML into scripts/lib/test_telemetry.py instead of flaky_aggregate.py.

Cron fires 30 min after mutation.yml on Monday (06:30 UTC = 14:30
Asia/Taipei). Commits the refreshed docs/test-telemetry/dashboard.md
back to main via git-auto-commit-action and posts a 5-line summary
to the Spidey Warnings channel via the existing
DISCORD_WEBHOOK_URL_WARNING secret (operator-supplied; gated on the
env so a missing secret produces a no-op rather than a failure).

timeout-minutes: 15 — artifact download + 100-artifact aggregation
fits comfortably in 5 min; 15 leaves headroom for unusual weeks.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §6.7 + §10 D4.4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: `docs/test-telemetry/` skeleton (D4.4 — part 4 of 4 + D4.6)

**Files:**

- Create: `docs/test-telemetry/.gitignore`
- Create: `docs/test-telemetry/README.md`
- Create: `docs/test-telemetry/dashboard.md`

- [ ] **Step 1: Create the directory + gitignore**

```bash
mkdir -p docs/test-telemetry
```

Open `docs/test-telemetry/.gitignore`:

```gitignore
# Transient SQLite cache used by the aggregator (committed dashboard is the SoT).
data.sqlite
data.sqlite-journal
```

- [ ] **Step 2: Write `docs/test-telemetry/README.md`**

Open `docs/test-telemetry/README.md`:

```markdown
# Test execution telemetry

Auto-regenerated weekly by `.github/workflows/test-telemetry.yml` and
`.github/workflows/mutation.yml`. All files in this directory are
**informational** — none of them is a CI gate; promotion is an operator
decision after telemetry shows stable readings.

## Contents

- `dashboard.md` — the rolling 7-day test health report (slow tests, flaky
  candidates, P99 outliers). Refreshed every Monday 06:30 UTC.
- `mutation-YYYY-MM-DD.md` — per-week mutation-testing run output. The
  mutation workflow opens a tracking issue (label `tech-debt-tests`) when
  any module is below the Phase 4 exit gate (60% killed).

## Reading the dashboard

- **Slow tests (top 30 by P99)** — anything with P99 > 30s is also called
  out separately under "Slow-tier warnings". Move it to the heavy tier
  (`@pytest.mark.heavy`) if it does not fit the fast-tier budget.
- **Flaky candidates** — failure rate > 1% over the last 7 days. The
  `flaky-tracker.yml` workflow auto-opens a tracking issue; the
  `.claude/rules/testing.md` quarantine workflow then governs the 14-day
  fix + 21-day delete SLO.

## Where the data comes from

`scripts/lib/test_telemetry.py` walks every `junit-*` artifact uploaded
by any workflow in the last 7 days (via the GitHub Actions REST API,
filtered by `artifact.created_at`), reads every `testcase` row, and
aggregates per-test stats. There is no persistent DB; the rolling
window is the artifact retention period (90 days for `ubuntu-latest`
public-repo runs).

## Adding a new workflow to telemetry

The aggregator picks up any artifact whose name starts with `junit-`.
A new workflow only has to ship its JUnit XML under that prefix:

\`\`\`yaml

- name: Upload JUnit
  if: always()
  uses: actions/upload-artifact@<pinned-sha>
  with:
  name: junit-<workflow-name>-${{ github.run_id }}
  path: <path-to-junit.xml>
  \`\`\`

No code change to `scripts/lib/test_telemetry.py` required.
```

- [ ] **Step 3: Write the placeholder `dashboard.md`**

Open `docs/test-telemetry/dashboard.md`:

```markdown
# Test execution telemetry dashboard

_Last updated: pending first cron run (regenerated weekly by `.github/workflows/test-telemetry.yml`)._

Total tests tracked: **0**.

## Slow tests (top 30 by P99)

| Test | P50 (s) | P95 (s) | P99 (s) | Runs |
| ---- | ------: | ------: | ------: | ---: |

_Awaiting first weekly run._

## Flaky candidates (failure rate > 1%)

None this week. ✓

## Slow-tier warnings (P99 > 30s)

None this week. ✓
```

- [ ] **Step 4: Commit**

```bash
git add docs/test-telemetry/
git commit -m "$(cat <<'EOF'
docs(test-telemetry): skeleton directory + README + placeholder dashboard

Phase 4 D4.4 part 4 of 4 + D4.6. Creates docs/test-telemetry/ with:

- README.md explaining the directory's role + "where the data comes
  from" + "adding a new workflow to telemetry" recipe.
- dashboard.md placeholder so the file exists before the first cron
  firing (avoids the "stefanzweifel/git-auto-commit-action with no
  change" no-op race).
- .gitignore for the transient SQLite cache.

The first run of test-telemetry.yml will overwrite dashboard.md;
mutation.yml writes its own dated reports beside it.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §6.7 + §10 D4.4 + D4.6

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: Heavy real-MLflow ACL multi-user (§10 #30 carryover — D2.3 #9)

**Files:**

- Create: `backend/tests/heavy/mlflow/test_acl_real_multi_user.py`

- [ ] **Step 1: Write the heavy test**

Open `backend/tests/heavy/mlflow/test_acl_real_multi_user.py`:

```python
"""§10 #30 carryover — D2.3 Task 9 (real-MLflow ACL multi-user).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 #30.
Predecessor: backend/tests/integration/routers/test_mlflow_authz.py covers
the ACL matrix with respx-mocked MLflow. This module locks the same
contract against a real MLflow 3.x server (testcontainers fixture
mlflow_url from backend/tests/heavy/conftest.py).

The invariant under test: experiments_proxy._mlflow_user_filter built
from user U1's UUID restricts search_runs to runs whose
tags["lolday.user_id"] == U1; runs created with U2's tag must not
appear in U1's filtered view. This is the production guarantee that
prevents user A from seeing user B's MLflow runs.

Marked heavy → runs in backend-slow.yml on main push + nightly.
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.heavy, pytest.mark.asyncio, pytest.mark.no_mock_mlflow]


def _build_user_filter(user_id: uuid.UUID) -> str:
    """Mirror of app.routers.experiments_proxy._mlflow_user_filter — kept
    inline so this heavy test does not need the FastAPI app to bootstrap."""
    return f"tags.\"lolday.user_id\" = '{user_id!s}'"


async def _create_tagged_run(
    http: httpx.AsyncClient,
    *,
    experiment_id: str,
    user_id: uuid.UUID,
) -> str:
    """Create an MLflow run carrying the tags.lolday.user_id=<U> tag."""
    start_ms = int(time.time() * 1000)
    resp = await http.post(
        "/api/2.0/mlflow/runs/create",
        json={
            "experiment_id": experiment_id,
            "start_time": start_ms,
            "tags": [{"key": "lolday.user_id", "value": str(user_id)}],
        },
    )
    resp.raise_for_status()
    return resp.json()["run"]["info"]["run_id"]


async def _search_runs(
    http: httpx.AsyncClient,
    *,
    experiment_id: str,
    filter_string: str,
) -> list[str]:
    resp = await http.post(
        "/api/2.0/mlflow/runs/search",
        json={
            "experiment_ids": [experiment_id],
            "filter": filter_string,
            "max_results": 100,
        },
    )
    resp.raise_for_status()
    return [r["info"]["run_id"] for r in resp.json().get("runs", [])]


@pytest.mark.asyncio
@pytest.mark.timeout(120)  # container boot + image pull may exceed the 30s default
async def test_mlflow_user_filter_restricts_to_owner(mlflow_url: str) -> None:
    """U1 creates run R1; U2 creates run R2; the U1-filter must see only R1."""
    u1 = uuid.uuid4()
    u2 = uuid.uuid4()
    async with httpx.AsyncClient(base_url=mlflow_url, timeout=30.0) as http:
        # Create an experiment for the test.
        exp_resp = await http.post(
            "/api/2.0/mlflow/experiments/create",
            json={"name": f"acl-multi-user-{int(time.time())}"},
        )
        exp_resp.raise_for_status()
        experiment_id = exp_resp.json()["experiment_id"]

        r1 = await _create_tagged_run(http, experiment_id=experiment_id, user_id=u1)
        r2 = await _create_tagged_run(http, experiment_id=experiment_id, user_id=u2)

        # U1's filter must see R1 only.
        u1_runs = await _search_runs(http, experiment_id=experiment_id, filter_string=_build_user_filter(u1))
        assert r1 in u1_runs
        assert r2 not in u1_runs, (
            f"U1 filter leaked U2 run {r2!r}; this is the cross-user ACL bug "
            f"the _mlflow_user_filter guard exists to prevent. Got: {u1_runs}"
        )

        # And U2's filter must see R2 only.
        u2_runs = await _search_runs(http, experiment_id=experiment_id, filter_string=_build_user_filter(u2))
        assert r2 in u2_runs
        assert r1 not in u2_runs


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_mlflow_admin_unscoped_search_sees_all(mlflow_url: str) -> None:
    """Without the user filter, both runs are visible (admin path)."""
    u1 = uuid.uuid4()
    u2 = uuid.uuid4()
    async with httpx.AsyncClient(base_url=mlflow_url, timeout=30.0) as http:
        exp_resp = await http.post(
            "/api/2.0/mlflow/experiments/create",
            json={"name": f"acl-admin-{int(time.time())}"},
        )
        exp_resp.raise_for_status()
        experiment_id = exp_resp.json()["experiment_id"]

        r1 = await _create_tagged_run(http, experiment_id=experiment_id, user_id=u1)
        r2 = await _create_tagged_run(http, experiment_id=experiment_id, user_id=u2)

        all_runs = await _search_runs(http, experiment_id=experiment_id, filter_string="")
        assert {r1, r2}.issubset(set(all_runs))
```

- [ ] **Step 2: Verify the test file is syntactically valid**

```bash
cd backend
uv run python -m compileall tests/heavy/mlflow/test_acl_real_multi_user.py
cd ..
```

Expected: no errors.

- [ ] **Step 3: (Optional) Run locally if Docker is available**

```bash
cd backend
uv run pytest tests/heavy/mlflow/test_acl_real_multi_user.py -m heavy -v
cd ..
```

Expected (with Docker): 2 passing tests. Expected (no Docker): tests skip cleanly because testcontainers fails to start.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/heavy/mlflow/test_acl_real_multi_user.py
git commit -m "$(cat <<'EOF'
test(heavy): real-MLflow ACL multi-user (§10 #30 D2.3 #9 closure)

Phase 4 closes the first of three §10 #30 deferrals. Spins two
distinct lolday user UUIDs, creates one MLflow run per user tagged
with tags.lolday.user_id=<UUID>, and asserts the
_mlflow_user_filter() string built from U1's UUID does NOT return
U2's run (and vice-versa).

The second case asserts an empty filter_string (the admin path)
returns both runs — proves the filter is the gate, not the
underlying MLflow visibility.

Runs against the existing heavy/conftest.py mlflow_url fixture
(real MLflow 3.x container, ~5s boot). Carries pytest.mark.heavy +
pytest.mark.no_mock_mlflow + pytest.mark.timeout(120) (image-pull
allowance).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 #30

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 19: Heavy audit-log durability on real PG (§10 #30 carryover — D2.3 #12)

**Files:**

- Create: `backend/tests/heavy/postgres/test_audit_log_durability.py`

- [ ] **Step 1: Write the heavy test**

Open `backend/tests/heavy/postgres/test_audit_log_durability.py`:

```python
"""§10 #30 carryover — D2.3 Task 12 (audit-log durability on real Postgres).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 #30.
Predecessor: backend/tests/integration/routers/test_audit_log.py exercises
write_audit_log on aiosqlite. This module locks the same contract on a
real Postgres 16 container via the heavy/conftest.py real_pg_session
fixture, covering the JSONB / FK-cascade / transactional-atomicity
invariants that aiosqlite cannot exercise:

- JSONB before_jsonb / after_jsonb round-trip (aiosqlite uses plain JSON
  via the with_variant binding).
- Append-only behaviour: a successful commit persists the row;
  a rolled-back transaction takes the audit row with it (single-commit
  semantics from services/audit.write_audit_log).
- Concurrent writes from two sessions land without conflict (no unique
  constraint violation; both rows visible after both commits).

The schema is created from app.models.* metadata so the test does not
depend on Alembic having run against the heavy container.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.heavy, pytest.mark.asyncio]


@pytest.fixture(scope="session", autouse=True)
async def _create_schema_on_real_pg(real_pg_engine):
    """Reflect app.models metadata onto the real PG schema once per session."""
    from app.models.user import Base

    async with real_pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # No teardown — testcontainers tears the whole container down.


@pytest.fixture
async def _seed_user(real_pg_session: AsyncSession):
    """Insert a User row that audit_log.actor_id can reference (FK ON DELETE RESTRICT)."""
    from app.models.user import Role, User

    u = User(id=uuid.uuid4(), email=f"u-{uuid.uuid4().hex[:8]}@dev.local", role=Role.user)
    real_pg_session.add(u)
    await real_pg_session.flush()
    return u


@pytest.mark.asyncio
async def test_audit_log_jsonb_roundtrip(real_pg_session: AsyncSession, _seed_user) -> None:
    """before/after dicts survive a commit + re-read against JSONB."""
    from app.models.audit import AuditLog
    from app.services.audit import write_audit_log

    target = uuid.uuid4()
    before = {"name": "old", "tags": ["a", "b"], "nested": {"k": 1}}
    after = {"name": "new", "tags": ["a", "b", "c"], "nested": {"k": 2}}
    await write_audit_log(
        real_pg_session,
        actor_id=_seed_user.id,
        action="test_update",
        target_type="dataset",
        target_id=target,
        before=before,
        after=after,
    )
    await real_pg_session.flush()

    row = (
        await real_pg_session.execute(
            select(AuditLog).where(AuditLog.target_id == target)
        )
    ).scalar_one()
    assert row.before_jsonb == before
    assert row.after_jsonb == after
    assert row.actor_id == _seed_user.id
    assert row.action == "test_update"
    assert row.target_type == "dataset"


@pytest.mark.asyncio
async def test_audit_log_rollback_takes_row_with_it(
    real_pg_engine, _seed_user
) -> None:
    """Real-PG: a rolled-back transaction must not leave an audit row."""
    from app.models.audit import AuditLog
    from app.services.audit import write_audit_log

    target = uuid.uuid4()
    SessionFactory = async_sessionmaker(real_pg_engine, expire_on_commit=False)
    async with SessionFactory() as s:
        await write_audit_log(
            s,
            actor_id=_seed_user.id,
            action="test_will_rollback",
            target_type="job",
            target_id=target,
            before=None,
            after=None,
        )
        # Caller-driven rollback — exactly the contract write_audit_log
        # promises (no commit inside the function).
        await s.rollback()

    # New session: confirm no row landed.
    async with SessionFactory() as s2:
        existing = (
            await s2.execute(select(AuditLog).where(AuditLog.target_id == target))
        ).scalar_one_or_none()
        assert existing is None


@pytest.mark.asyncio
async def test_audit_log_concurrent_writes_both_persist(
    real_pg_engine, _seed_user
) -> None:
    """Two sessions append simultaneously; both rows survive after both commit."""
    from app.models.audit import AuditLog
    from app.services.audit import write_audit_log

    SessionFactory = async_sessionmaker(real_pg_engine, expire_on_commit=False)
    target_a = uuid.uuid4()
    target_b = uuid.uuid4()

    async def _append(target: uuid.UUID, action: str) -> None:
        async with SessionFactory() as s:
            await write_audit_log(
                s,
                actor_id=_seed_user.id,
                action=action,
                target_type="model",
                target_id=target,
                before=None,
                after={"action_id": action},
            )
            await s.commit()

    await asyncio.gather(
        _append(target_a, "concurrent_a"),
        _append(target_b, "concurrent_b"),
    )

    async with SessionFactory() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.target_id.in_([target_a, target_b]))
            )
        ).scalars().all()
        actions = sorted(r.action for r in rows)
        assert actions == ["concurrent_a", "concurrent_b"]
```

- [ ] **Step 2: Verify the test file compiles**

```bash
cd backend
uv run python -m compileall tests/heavy/postgres/test_audit_log_durability.py
cd ..
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/heavy/postgres/test_audit_log_durability.py
git commit -m "$(cat <<'EOF'
test(heavy): audit-log durability on real Postgres (§10 #30 D2.3 #12 closure)

Phase 4 closes the second of three §10 #30 deferrals. Three cases
against the heavy/conftest.py real_pg_session + real_pg_engine
fixtures (Postgres 16 container):

1. JSONB before/after dict round-trip — proves the JSONB().with_variant
   binding survives a commit + re-read on real PG (aiosqlite path
   covers plain JSON only).
2. Rollback-takes-row-with-it — write_audit_log adds the row to the
   session but does NOT commit; if the caller rolls back, the audit
   row must not land. Real-PG transactional atomicity is the contract.
3. Concurrent two-session append — both rows persist after both commit;
   no unique-constraint conflict (the schema's only uniqueness is on
   the primary key, which is generated client-side via uuid.uuid4()).

Autouse fixture create_schema_on_real_pg reflects the User + AuditLog
tables onto the container once per session via Base.metadata.create_all
— heavy tests do not rely on Alembic having run.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 #30

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 20: Heavy JWKS reflector (§10 #30 carryover — D2.4 #13)

**Files:**

- Create: `backend/tests/heavy/auth/__init__.py`
- Create: `backend/tests/heavy/auth/test_jwks_reflector.py`

- [ ] **Step 1: Create the package marker**

```bash
mkdir -p backend/tests/heavy/auth
touch backend/tests/heavy/auth/__init__.py
```

- [ ] **Step 2: Write the heavy test**

Open `backend/tests/heavy/auth/test_jwks_reflector.py`:

```python
"""§10 #30 carryover — D2.4 Task 13 (JWKS reflector heavy).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 #30.
Predecessor: backend/tests/integration/services/test_jwks_cache_ttl.py
asserts the PyJWKClient instance is constructed with the right
cache_jwk_set / lifespan / lru_cache arguments (structural). This
module locks the _behavioural_ side: actually serve a JWKS at
/.well-known/jwks.json from a uvicorn process, mint an RSA key,
sign a JWT, hand it to the production code path (_get_jwks_client +
PyJWT verify), and verify the cache holds across freezegun-controlled
clock advances inside the 600s TTL.

uvicorn-as-test-fixture pattern: start uvicorn in a thread with
`Config(..., loop="asyncio", log_level="warning")` against a tiny
Starlette app, poll until the port responds, run the test body,
shut down cleanly via the Server.should_exit flag.

Marked heavy → runs in backend-slow.yml on main push + nightly.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import time

import httpx
import jwt as pyjwt
import pytest
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from freezegun import freeze_time
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

pytestmark = [pytest.mark.heavy]


def _mint_rsa() -> tuple[rsa.RSAPrivateKey, dict]:
    """Generate an RSA-2048 key + a JWKS-shaped public JWK dict."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()

    def _b64url(n: int) -> str:
        import base64

        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "kid": "test-key-1",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(numbers.n),
        "e": _b64url(numbers.e),
    }
    return key, jwk


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ReflectorServer:
    """Spin uvicorn in a background thread serving /.well-known/jwks.json."""

    def __init__(self) -> None:
        self.key, jwk = _mint_rsa()
        self.jwk = jwk
        self.jwks_fetch_count = 0
        self.port = _free_port()
        self.thread: threading.Thread | None = None
        self.server: uvicorn.Server | None = None
        self._lock = threading.Lock()

        async def jwks(request):
            with self._lock:
                self.jwks_fetch_count += 1
            return JSONResponse({"keys": [jwk]})

        app = Starlette(routes=[Route("/.well-known/jwks.json", jwks)])
        self.app = app

    def start(self) -> None:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        # Poll until the port responds.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with contextlib.suppress(httpx.HTTPError):
                resp = httpx.get(self.jwks_url, timeout=1.0)
                if resp.status_code == 200:
                    return
            time.sleep(0.05)
        raise RuntimeError("reflector server failed to start")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)

    @property
    def jwks_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/.well-known/jwks.json"

    def sign(self, payload: dict) -> str:
        """Sign a JWT with the reflector's RSA key."""
        pem = self.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return pyjwt.encode(payload, pem, algorithm="RS256", headers={"kid": self.jwk["kid"]})


@pytest.fixture
def reflector():
    s = _ReflectorServer()
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_jwks_client_verifies_signed_jwt_against_reflector(reflector: _ReflectorServer) -> None:
    """End-to-end: PyJWKClient fetches the reflector's JWKS and verifies a JWT."""
    client = pyjwt.PyJWKClient(reflector.jwks_url, cache_jwk_set=True, lifespan=600)
    token = reflector.sign({"sub": "u@example.com", "aud": "test-aud"})
    signing_key = client.get_signing_key_from_jwt(token).key
    claims = pyjwt.decode(token, signing_key, algorithms=["RS256"], audience="test-aud")
    assert claims["sub"] == "u@example.com"
    # First call hit the reflector once.
    assert reflector.jwks_fetch_count == 1


def test_jwks_client_cache_holds_inside_ttl(reflector: _ReflectorServer) -> None:
    """A second verify inside the TTL window must reuse the cached JWKS."""
    client = pyjwt.PyJWKClient(reflector.jwks_url, cache_jwk_set=True, lifespan=600)
    token1 = reflector.sign({"sub": "u1"})
    token2 = reflector.sign({"sub": "u2"})
    client.get_signing_key_from_jwt(token1)
    # Cache hit on the second call — fetch count must NOT increment.
    with freeze_time() as frozen:
        frozen.tick(delta=300)  # +5 min < 600s TTL
        client.get_signing_key_from_jwt(token2)
    assert reflector.jwks_fetch_count == 1, (
        f"JWKS cache leaked: expected 1 fetch within TTL, got {reflector.jwks_fetch_count}"
    )


def test_jwks_client_refreshes_after_ttl_expires(reflector: _ReflectorServer) -> None:
    """A verify after TTL expiry must re-fetch the JWKS."""
    client = pyjwt.PyJWKClient(reflector.jwks_url, cache_jwk_set=True, lifespan=600)
    client.get_signing_key_from_jwt(reflector.sign({"sub": "u1"}))
    assert reflector.jwks_fetch_count == 1
    # Advance past the TTL window.
    with freeze_time() as frozen:
        frozen.tick(delta=601)
        client.get_signing_key_from_jwt(reflector.sign({"sub": "u2"}))
    assert reflector.jwks_fetch_count == 2, (
        f"JWKS cache did not refresh after TTL expiry; "
        f"expected 2 fetches, got {reflector.jwks_fetch_count}"
    )
```

- [ ] **Step 3: Verify the test compiles**

```bash
cd backend
uv run python -m compileall tests/heavy/auth/test_jwks_reflector.py
cd ..
```

- [ ] **Step 4: (Optional) Run locally — does not need Docker, uses an in-process uvicorn**

```bash
cd backend
uv run pytest tests/heavy/auth/test_jwks_reflector.py -m heavy -v
cd ..
```

Expected: 3 passing tests (this one is unusual among heavy tests — no testcontainers, so it works locally without Docker).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/heavy/auth/__init__.py backend/tests/heavy/auth/test_jwks_reflector.py
git commit -m "$(cat <<'EOF'
test(heavy): JWKS reflector behavioural test (§10 #30 D2.4 #13 closure)

Phase 4 closes the third (and final) §10 #30 deferral. Three cases:

1. End-to-end signed-JWT verify via PyJWKClient against an in-process
   uvicorn-served /.well-known/jwks.json with a freshly minted
   RSA-2048 key.
2. Cache hold inside the 600s TTL — freezegun advances +5 min;
   reflector fetch count must stay at 1.
3. Cache refresh after TTL expiry — freezegun advances +601s; fetch
   count must increment to 2.

Unique among heavy tests: no testcontainers / Docker dependency.
uvicorn-in-a-thread + Starlette /.well-known/jwks.json + manual port
poll == fully self-contained behavioural fixture.

Closes architecture.md §10 #30 fully (all three Phase 2/3-deferred
heavy items now have coverage).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 #30

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 21: `.claude/rules/scripts-and-ops.md` — R6 rule (D4.5)

**Files:**

- Modify: `.claude/rules/scripts-and-ops.md` (append `## R6 — Touched script must add lib + test` section)

- [ ] **Step 1: Append the new section**

Open `.claude/rules/scripts-and-ops.md` and append (after the existing `## CI` section at the end):

```markdown
## R6 — Touched script must add lib + test

Phase 4 D4.5. When a PR modifies any script under `scripts/`:

- **Pure shell changes** (renaming, refactoring shell control flow, fixing
  a flag-parse bug): add or extend a `tests/bats/<script>_smoke.bats` case
  that covers the changed path. The bats workflow (`.github/workflows/bats.yml`)
  enforces in CI.
- **Embedded `python3 -<<'PY' ... PY` heredoc changes**: do NOT modify in
  place. Extract the heredoc into a `scripts/lib/<topic>.py` module (named
  by the area it serves — `harbor_api`, `helpers_lock`, etc.), invoke from
  bash via `python3 -m scripts.lib.<topic> <verb>`, and add a pytest unit
  at `scripts/tests/lib/test_<topic>.py`. The backend-fast.yml workflow
  runs `pytest ../scripts/tests/lib/` as part of its existing pytest
  invocation.
- **New scripts**: ship with both a bats smoke and (if the script does
  non-trivial Python or HTTP work) a pytest unit from day 1.

The rationale (R6 in `docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md` §9):
PR #184 (Python heredoc bug in `build-helpers.sh`) and PR #155
(apostrophe escape in `recover-harbor.sh`) both shipped without a test
gate — bats + pytest would have caught them. The cost of the test is
linear in the change size; the cost of a regression is unbounded.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/rules/scripts-and-ops.md
git commit -m "$(cat <<'EOF'
docs(rules): scripts-and-ops R6 — touched script must add lib + test

Phase 4 D4.5. Codifies the rule established by R6 in the test
architecture spec: any PR touching scripts/ either ships a bats
smoke (pure shell changes) or extracts the python heredoc into
scripts/lib/ + a pytest unit (python heredoc changes). The PR #184
and PR #155 incidents motivated the rule; the tooling shipped in
Tasks 1-9 makes it actionable.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §9 R6 + §10 D4.5

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 22: §10 #30 close + Phase 4 entry in `docs/architecture.md`

**Files:**

- Modify: `docs/architecture.md` §10 — flip #30 to resolved; append #31 Phase 4 ship note

- [ ] **Step 1: Update §10 #30 status**

In `docs/architecture.md`, locate the §10 #30 entry (around line 530). Replace it with:

```markdown
30. ~~**Phase 2 deferred follow-ups (partial close 2026-05-16; heavy testcontainers tier still pending)**~~ — fully resolved 2026-05-16 in Phase 4 (`feat/test-architecture-phase-4`). The three remaining heavy-tier items shipped: (a) D2.3 #9 real-MLflow ACL multi-user via `backend/tests/heavy/mlflow/test_acl_real_multi_user.py` (two-user filter isolation contract on real MLflow 3.x); (b) D2.3 #12 audit-log durability on real Postgres via `backend/tests/heavy/postgres/test_audit_log_durability.py` (JSONB round-trip + rollback-takes-row-with-it + concurrent two-session writes); (c) D2.4 #13 JWKS reflector via `backend/tests/heavy/auth/test_jwks_reflector.py` (uvicorn-served minted-RSA JWKS + freezegun-controlled 600s TTL cache hold/refresh). Phase 4 also ships D4.1 bats (`.github/workflows/bats.yml`), D4.2 R6 kick-off (`scripts/lib/harbor_api.py` + `helpers_lock.py` + pytest at `scripts/tests/lib/`), D4.3 weekly mutation cron (`.github/workflows/mutation.yml` + top-10 module config in `backend/pyproject.toml`), D4.4 weekly test telemetry (`.github/workflows/test-telemetry.yml` + `scripts/lib/test_telemetry.py` + `docs/test-telemetry/dashboard.md`), D4.5 scripts-touched-need-lib-and-test rule in `.claude/rules/scripts-and-ops.md`, and D4.6 dashboard skeleton.
```

- [ ] **Step 2: Verify the change is well-formed**

```bash
grep -A 1 "^30\." docs/architecture.md | head -5
```

Expected: the new ~~struck-through~~ entry plus the next item.

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs(architecture): close §10 #30 with Phase 4 ship summary

Phase 4 closure entry. #30 was the "Phase 2 deferred follow-ups" item
covering the three heavy-tier tests Phase 2 + 3 did not have Docker
+ testcontainers scaffolding bandwidth for. All three landed in
Phase 4 (Tasks 18 / 19 / 20). The entry also names the other Phase 4
deliverables (D4.1-D4.6) so future maintainers can trace any of
them back to one tech-debt entry rather than hunting through plan
files.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 Phase 4 exit

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 23: Full local verification — bats + pytest fast + scripts-lib tests + heavy tests (locally if Docker)

**Goal:** Run every test surface Phase 4 added (or modified) to confirm green before opening the PR.

- [ ] **Step 1: Run pytest fast tier + new scripts-lib tests**

```bash
cd backend
uv run pytest -m "not heavy" -q
uv run pytest ../scripts/tests/lib/ -v
cd ..
```

Expected: all green. If anything red, fix in a follow-up commit before proceeding to Task 24.

- [ ] **Step 2: Run bats locally (skip if bats not installed)**

```bash
if command -v bats >/dev/null 2>&1; then
  bats tests/bats/
elif [ -x tests/bats/bats-core/bin/bats ]; then
  ./tests/bats/bats-core/bin/bats tests/bats/
else
  echo "bats unavailable locally; CI run in Task 24 covers it."
fi
```

Expected: 9 passing tests (4 from check-helpers-lock + 5 from build-helpers).

- [ ] **Step 3: Run pre-commit on the full diff**

```bash
pre-commit run --from-ref origin/main --to-ref HEAD
```

Expected: all hooks pass. If ruff or prettier reformats anything, commit the fixups.

- [ ] **Step 4: (Optional) Run the JWKS reflector heavy test locally — no Docker required**

```bash
cd backend
uv run pytest tests/heavy/auth/test_jwks_reflector.py -m heavy -v
cd ..
```

Expected: 3 passing tests. The MLflow + PG heavy tests need Docker and are validated by CI's `backend-slow.yml` job.

- [ ] **Step 5: No commit — this task is verification only**

If Steps 1-4 produced any changes (e.g. a pre-commit reformat), commit them as a `chore(format): pre-commit fixups` commit before Task 24.

---

### Task 24: Open PR, monitor CI, squash merge

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/test-architecture-phase-4
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "feat(test-architecture): Phase 4 — bats + R6 lib + mutation + telemetry + §10 #30 closure" --body "$(cat <<'EOF'
## Summary

Phase 4 of the test architecture redesign:
- **D4.1** bats GHA action (`bats-core/bats-action@3.0.0`) + `tests/bats/{check_helpers_lock,build_helpers}_smoke.bats` + new informational `bats.yml` workflow.
- **D4.2** R6 kick-off: `scripts/lib/harbor_api.py` + `scripts/lib/helpers_lock.py` extracted from `build-helpers.sh` + `recover-harbor.sh` + `check-helpers-lock.sh`; pytest units at `scripts/tests/lib/`; `backend-fast.yml` extended to run them.
- **D4.3** Weekly mutation cron (`mutation.yml` Monday 06:00 UTC) → mutmut against top-10 risk modules → `docs/test-telemetry/mutation-<date>.md` committed back to main; auto-issue if any module < 60% killed.
- **D4.4** Weekly test-telemetry cron (`test-telemetry.yml` Monday 06:30 UTC) → aggregates JUnit XML artifacts via `scripts/lib/test_telemetry.py` → `docs/test-telemetry/dashboard.md` + Spidey Warnings Discord summary.
- **D4.5** `.claude/rules/scripts-and-ops.md` §R6 rule codified.
- **D4.6** `docs/test-telemetry/` skeleton + README.
- **§10 #30 closure**: 3 heavy backend tests (real-MLflow ACL multi-user, real-PG audit-log durability, uvicorn-served JWKS reflector w/ freezegun-controlled cache TTL).

## Test plan

- [ ] `bats` workflow green on PR.
- [ ] `backend-fast` workflow green (covers `scripts/tests/lib/` via the new step).
- [ ] `backend-slow` workflow green on main push (covers the three new heavy tests).
- [ ] First post-merge `mutation.yml` cron firing produces a `mutation-<date>.md` and doesn't open a `BELOW 60%` issue.
- [ ] First post-merge `test-telemetry.yml` cron firing produces a refreshed `dashboard.md` and posts to Spidey Warnings.
- [ ] `gh pr checks` shows all 9 required contexts green (branch protection unchanged).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Watch CI**

```bash
gh pr checks --watch
```

Expected: all 9 required contexts pass + the new `bats` informational gate passes. Phase 4 cron workflows (`mutation`, `test-telemetry`) do NOT fire on PR push (cron + workflow_dispatch only).

- [ ] **Step 4: If any required check fails, fix and re-push**

For backend / pytest failures, the most likely cause is a heavy-tier import path or fixture mismatch — re-run locally with the exact failing test name. For bats failures, check the `bats-core/bats-action` SHA pin (Task 3) and the `tests/bats/<name>.bats` setup() / teardown() blocks. For pre-commit reformat failures in CI, re-run `pre-commit run --all-files` locally and commit.

- [ ] **Step 5: Squash merge**

```bash
gh pr merge --squash --auto
```

Expected: branch deletes itself; squash commit lands on main with the PR body as the commit message.

- [ ] **Step 6: Update `MEMORY.md` index entry**

After merge, write a new memory file `project_test_architecture_phase_4_shipped.md` capturing:

- PR / squash SHA
- What shipped (D4.1 – D4.6 + §10 #30 closure)
- Any deviation from the plan
- Lessons learned (for the Phase 5 plan if Phase 5 is ever triggered)

Then add a single-line index entry to `MEMORY.md` per the auto-memory convention.

(This step is non-blocking — the auto-memory writeup happens in the assistant turn, not the plan execution.)

---

## Exit criteria

Phase 4 is "shipped" when:

- All 24 tasks above are committed and the squash PR is merged into `main`.
- All 9 required branch-protection contexts are green.
- The new `bats.yml` workflow is green on its first cron + PR firings.
- The first `mutation.yml` firing produces a `mutation-<date>.md` (does NOT have to be all-clean; the gate is "the cron runs and emits something", not "kill-rate ≥ 80%" — that is the eventual Phase 4 target, not the ship gate).
- The first `test-telemetry.yml` firing refreshes `dashboard.md` and posts to Spidey Warnings (no `@here`).
- `docs/architecture.md` §10 #30 reads as resolved.

Promotion of any new gate to a required check stays an operator decision after two consecutive green cron firings.
