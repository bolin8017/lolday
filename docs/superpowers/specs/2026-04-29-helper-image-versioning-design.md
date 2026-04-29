# Helper Image Versioning — Design Specification

## Overview

Lolday builds two platform-side helper images and pins them by hand:

- `harbor.lolday.svc:80/lolday/build-helper:v3` — build-pipeline init container that validates a detector against the maldet spec and emits build-args.
- `harbor.lolday.svc:80/lolday/job-helper:v4` — job-pod init/sidecar that writes config, fetches source models, and tails events.

Today the operator edits a `Dockerfile`, runs `docker build` + `docker push` from memory, and bumps the `:vN` tag in `backend/app/config.py` and `charts/lolday/values.yaml`. The recipe lives only in scattered phase-history checklists. Both shortcomings are recorded as tech debt in `docs/architecture.md` §9 #4 (helper images built by hand) and #8 (helper image versions hardcoded, no versioning strategy).

This phase replaces the manual recipe with a content-addressable, git-tracked release flow:

- Each helper image carries a tag derived from its source subtree's git tree SHA, so identical content yields identical tags and the rebuild is idempotent.
- A new `scripts/build-helpers.sh` computes the SHA, builds, pushes (idempotent against Harbor), and writes `charts/lolday/helpers.lock`.
- `scripts/deploy.sh` reads the lock and injects the image refs via Helm `--set`. `backend/app/config.py` defaults move to empty strings with a production fail-fast model_validator.
- `charts/lolday/values.yaml` drops the hardcoded helper-image keys; the duplicated dead `jobs.helperImage` is removed.
- A pre-commit hook prevents committing helper-source changes without re-running the build.

The release runbook lives in a new `docs/runbooks/release-helpers.md`. CI integration is out of scope for this phase; the script is designed to be CI-callable.

## Authorization

Breaking changes are explicitly authorized:

- Removing `BUILD_IMAGE_HELPER` and `JOB_HELPER_IMAGE` defaults from `backend/app/config.py` (no backwards-compatibility shim).
- Deleting `backend.env.BUILD_IMAGE_HELPER`, `backend.env.JOB_HELPER_IMAGE`, and `jobs.helperImage` from `charts/lolday/values.yaml`.
- Removing the helper-image build/push lines from `scripts/recover-harbor.sh` (the recovery flow delegates to `scripts/build-helpers.sh`).
- Adding a mandatory step ("run `bash scripts/build-helpers.sh`") to first-time bootstrap before `bash scripts/deploy.sh` works.

## Scope

### In scope

1. `scripts/build-helpers.sh` — computes per-helper subtree SHA, idempotently builds and pushes to Harbor, writes `charts/lolday/helpers.lock` atomically.
2. `charts/lolday/helpers.lock` — JSON, git-tracked, single source of truth for the helper image refs deployed today.
3. `backend/app/config.py` — drop hardcoded `BUILD_IMAGE_HELPER` / `JOB_HELPER_IMAGE` defaults; add `validate_helper_images` model_validator that fails boot in production when either is empty.
4. `charts/lolday/values.yaml` — remove `backend.env.BUILD_IMAGE_HELPER`, `backend.env.JOB_HELPER_IMAGE`, and `jobs.helperImage`.
5. `scripts/deploy.sh` — drift-guard the lock, inject the two image refs via `--set backend.env.*`.
6. `scripts/recover-harbor.sh` — drop the helper-image lines; delegate to `scripts/build-helpers.sh` after Harbor is back.
7. Pre-commit hook — refuse to commit when a helper subtree's SHA disagrees with the lock.
8. `docs/runbooks/release-helpers.md` — new runbook covering the standard flow, dry-run, single-helper, dirty-tree, rollback, and a CI sketch.
9. Documentation updates: `README.md` setup step, root `CLAUDE.md` Quickstart, `docs/runbooks/deploy.md` §5, `docs/architecture.md` §9 #4 / #8 marked resolved, `.claude/rules/charts-and-helm.md` and `.claude/rules/scripts-and-ops.md`.

### Out of scope

- **`mlflow-server` and `pytorch-cu12-base` versioning.** Their tags carry external semantic meaning (upstream MLflow `v2.20.3`, CUDA + torch wheel set `2.7.0-cu126`); subtree SHA strips that signal. A future phase can extend `helpers.lock` with a different keying rule (e.g. parse the `FROM` line in `Dockerfile`).
- **CI / GitHub Actions integration.** The script is pure-functional (input = git tree, output = lock + Harbor pushes), so a future CI yaml can call it directly.
- **Image digest pinning** (`@sha256:…`). Future hardening; tag-level content addressing is the floor.
- **Multi-arch build.** server30 is amd64 only.
- **Cleaning up the other dead `jobs.*` keys** (`activeDeadlineSeconds`, `perUserConcurrency`, `idempotencyWindowSeconds`). Real values come from `backend.env.*` already; the dead keys deserve their own values.yaml-hygiene phase.
- **Garbage collecting the legacy `:v3` / `:v4` tags from Harbor.** Manual one-shot UI cleanup after migration; not a code change.

## Architecture

### Versioning strategy

Per helper, the tag is the first 12 hex digits of the subtree's tree object SHA at `HEAD`:

```bash
SHA=$(git rev-parse --short=12 "HEAD:charts/lolday/helpers/${NAME}")
# → e.g. a1b2c3d4e5f6
```

Properties:

- **Content-addressable.** Any committed change inside the subtree (`Dockerfile`, `pyproject.toml`, `uv.lock`, source files, tests) shifts the SHA. No change in the subtree leaves it unchanged.
- **Deterministic across operators.** `git rev-parse HEAD:<path>` reads the committed tree object; two operators on the same commit yield the same SHA.
- **Length.** 12 hex digits (48 bits) sits between git's default 7 and the SHA's full 40. Linux kernel uses 12 by convention; the head-room over 7 buys collision resistance for the foreseeable life of the project at trivial readability cost.
- **Industry alignment.** Argo CD / Flux gitops practices, Bazel `rules_oci`, skaffold's `tagPolicy: gitCommit`, and Google Cloud's container-tagging guidance all converge on commit-derived tags. Subtree SHA is the monorepo-aware refinement.

Full image ref shape: `harbor.lolday.svc:80/lolday/<name>:<sha>`. The `harbor.lolday.svc:80` prefix matches the host `/etc/hosts` + K3s containerd registry mirror — see `docs/architecture.md` §5.3.

### `scripts/build-helpers.sh` contract

```
Usage: scripts/build-helpers.sh [--allow-dirty] [--dry-run] [--only NAME]
```

The helper list lives in a `HELPERS=(build-helper job-helper)` array at the top of the script. Adding a future helper is a one-line edit.

#### Authentication

Pulls the robot credentials out of the existing K8s Secret rather than asking for `HARBOR_ADMIN_PASSWORD`:

```bash
kubectl -n lolday get secret harbor-push-cred \
  -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d
# → JSON containing auth for "robot$build-pusher" against harbor.lolday.svc:80
```

The script decodes the base64 auth field, splits username:secret, and runs `docker login harbor.lolday.svc.cluster.local:80 -u 'robot$build-pusher' --password-stdin`. If the Secret is missing the script exits with a friendly message pointing at `scripts/recover-harbor.sh`. The `repository:pull` scope on the robot account already grants the read-only Harbor API needed for the idempotency check; no admin password is required.

#### Per-helper steps

1. Compute the subtree SHA.
2. Idempotency check: `GET /api/v2.0/projects/lolday/repositories/<name>/artifacts?q=tags=<sha>` against Harbor. A 200 with a non-empty `tags` array means the image already exists — print `[skip] <name>:<sha>`, advance.
3. On a miss: `docker build --pull -t harbor.lolday.svc.cluster.local:80/lolday/<name>:<sha> charts/lolday/helpers/<name>` then `docker push`.
4. Record `<helper_key>=<full-image-ref>` in memory.

#### Dirty-tree handling

Before computing each SHA, the script asserts the subtree is clean:

```bash
git diff --quiet HEAD -- "charts/lolday/helpers/${NAME}"
test -z "$(git ls-files --others --exclude-standard "charts/lolday/helpers/${NAME}")"
```

A failure (uncommitted modifications or untracked files) exits 1 with a message naming the offending paths. `--allow-dirty` skips the check, appends `-dirty-<unix-ts>` to the tag, builds and pushes the dirty image to Harbor, and **does not write the lock**. Splitting the dirty path away from the lock keeps unreproducible tags out of git history.

#### Shallow-clone guard

`git rev-parse --is-shallow-repository` returning `true` exits 1 with an instruction to run `git fetch --unshallow`. The bare `git rev-parse HEAD:<path>` would otherwise fail with a less actionable "missing tree" message.

#### Atomic lock write

After all helpers finish (and the run is not `--allow-dirty` or `--dry-run`), the script writes a temp file, `mv`s it onto `charts/lolday/helpers.lock`, and prints either `[lock] updated` (with a list of changed entries) or `[lock] unchanged`. Exit 0.

#### Flag semantics

| Flag            | Build / push?   | Writes lock?                | Tag form           |
| --------------- | --------------- | --------------------------- | ------------------ |
| (default)       | yes, idempotent | yes                         | `<sha>`            |
| `--dry-run`     | no              | no                          | (printed)          |
| `--allow-dirty` | yes             | no                          | `<sha>-dirty-<ts>` |
| `--only NAME`   | only `<NAME>`   | yes (only that key updated) | `<sha>`            |

### `charts/lolday/helpers.lock`

Format: pretty-printed JSON, trailing newline, repository-tracked.

```json
{
  "build_helper": "harbor.lolday.svc:80/lolday/build-helper:abc123def456",
  "job_helper": "harbor.lolday.svc:80/lolday/job-helper:0123456789ab"
}
```

- **Path.** `charts/lolday/helpers.lock`, alongside `helpers/`. `.helmignore` excludes it from the chart artefact (the lock is build metadata, not chart payload).
- **Keys.** snake_case (`build_helper`, `job_helper`) matching the lowercase-snake form of the helper name.
- **Values.** Fully-qualified image refs including host:port, project, name, and tag — letting deploy.sh and any future consumer treat the value as opaque.
- **Encoding.** JSON over env-style: extensible (digest, build-date, multi-arch all fit later as new fields), parseable by the python3 already in deploy.sh, and bytewise-stable across reformatters.

### `scripts/deploy.sh` injection

Inserted into the pre-flight block (after the existing secrets / kubectl checks, before `helm upgrade`):

```bash
HELPERS_LOCK="$CHART_DIR/helpers.lock"
[ -f "$HELPERS_LOCK" ] || {
  echo "ERROR: $HELPERS_LOCK missing — run 'bash scripts/build-helpers.sh' first" >&2
  exit 1
}

# Drift guard — lock SHA must match the current HEAD subtree SHA.
DRIFT=$(python3 - <<'PY' "$HELPERS_LOCK"
import json, subprocess, sys
lock = json.load(open(sys.argv[1]))
out = []
for key, ref in lock.items():
    helper = key.replace("_", "-")
    sha = subprocess.check_output(
        ["git", "rev-parse", "--short=12", f"HEAD:charts/lolday/helpers/{helper}"],
        text=True,
    ).strip()
    if not ref.endswith(f":{sha}"):
        out.append(f"  {helper}: lock={ref} HEAD=...:{sha}")
print("\n".join(out))
PY
)
[ -z "$DRIFT" ] || {
  echo "ERROR: helpers.lock drift detected:" >&2
  echo "$DRIFT" >&2
  echo "Run 'bash scripts/build-helpers.sh' and commit the updated lock." >&2
  exit 1
}

BUILD_IMAGE_HELPER=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["build_helper"])' "$HELPERS_LOCK")
JOB_HELPER_IMAGE=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["job_helper"])'  "$HELPERS_LOCK")
```

Two `--set` lines join the existing helm command:

```bash
--set backend.env.BUILD_IMAGE_HELPER="$BUILD_IMAGE_HELPER" \
--set backend.env.JOB_HELPER_IMAGE="$JOB_HELPER_IMAGE" \
```

The injected keys flow through `templates/backend.yaml`'s existing `{{- range $k, $v := .Values.backend.env }}` block — no template change required.

### `backend/app/config.py` changes

Defaults move to empty strings; a new model_validator enforces non-empty in production. The shape mirrors the existing `validate_sso_config`:

```python
BUILD_IMAGE_HELPER: str = ""
JOB_HELPER_IMAGE: str = ""

@model_validator(mode="after")
def validate_helper_images(self) -> "Settings":
    """Fail-fast on production misconfiguration. Helper images must be
    pinned via charts/lolday/helpers.lock (produced by
    scripts/build-helpers.sh) and injected by scripts/deploy.sh — never
    hardcoded as defaults."""
    if self.ENVIRONMENT != "production":
        return self
    missing = [
        name for name in ("BUILD_IMAGE_HELPER", "JOB_HELPER_IMAGE")
        if not getattr(self, name)
    ]
    if missing:
        raise ValueError(
            f"{', '.join(missing)} must be set in production. "
            "Produce the values via scripts/build-helpers.sh and inject "
            "them via scripts/deploy.sh."
        )
    return self
```

The two validators stay separate (one concern apiece). Tests run with `ENVIRONMENT=development` (existing default) and pass through unchanged.

### `charts/lolday/values.yaml` changes

Three lines disappear:

| Line (current)                                                                  | Action                                         |
| ------------------------------------------------------------------------------- | ---------------------------------------------- |
| `backend.env.BUILD_IMAGE_HELPER: "harbor.lolday.svc:80/lolday/build-helper:v3"` | delete                                         |
| `backend.env.JOB_HELPER_IMAGE: "harbor.lolday.svc:80/lolday/job-helper:v4"`     | delete                                         |
| `jobs.helperImage: harbor.lolday.svc:80/lolday/job-helper:v4`                   | delete (dead config — no template consumes it) |

No template change. The backend.yaml `range` over `.Values.backend.env` automatically includes whatever `--set` adds. Backend pod env loses these two keys until `deploy.sh` injects them; production boots fail fast via `validate_helper_images` if injection is skipped.

`jobs.networkPolicy.enabled` (the only live `jobs.*` consumer, used by `templates/job-networkpolicy.yaml`) stays untouched. Other dead `jobs.*` keys (`activeDeadlineSeconds`, `perUserConcurrency`, `idempotencyWindowSeconds`) also stay — out of scope.

### `scripts/recover-harbor.sh` cleanup

The bottom of `recover-harbor.sh` currently rebuilds and pushes a fixed list of images:

```bash
build_push      "$REPO/backend"                            "lolday/lolday-backend:phase9.5"
build_push      "$REPO/charts/lolday/helpers/build-helper" "lolday/build-helper:v2"
skip_if_missing "$REPO/charts/lolday/helpers/job-helper"   "lolday/job-helper:v2"
skip_if_missing "$REPO/charts/lolday/helpers/mlflow-server" "lolday/mlflow-server:v2.20.3"
skip_if_missing "$REPO/frontend"                            "lolday/lolday-frontend:phase5"
```

The `build-helper:v2` and `job-helper:v2` tags were already stale before this phase. Root cause: `recover-harbor.sh` mixes Harbor-recovery responsibility with helper-image release responsibility. The fix splits them:

- Remove the `build-helper` and `job-helper` lines.
- After the recovery body completes, if `charts/lolday/helpers.lock` exists, exec `bash "$REPO/scripts/build-helpers.sh"` (idempotent — already-pushed SHAs short-circuit). Otherwise print a warning naming the next manual step.
- Lines for `mlflow-server`, `frontend`, `backend` stay (out of scope for this phase).

### Pre-commit hook

A new local hook in `.pre-commit-config.yaml`:

```yaml
- id: helpers-lock-fresh
  name: helpers.lock matches helper subtrees
  entry: scripts/check-helpers-lock.sh
  language: script
  pass_filenames: false
  stages: [pre-commit]
  files: ^charts/lolday/helpers/(build-helper|job-helper)/|^charts/lolday/helpers\.lock$
```

`scripts/check-helpers-lock.sh` is `scripts/build-helpers.sh --dry-run` plus a final exit-code conversion: if any helper's HEAD subtree SHA disagrees with the lock entry, exit 1 and print the same friendly drift message deploy.sh uses. Hook fires whenever a `helpers/{build-helper,job-helper}/...` file is staged or `helpers.lock` itself is staged. An environment escape hatch — `LOLDAY_SKIP_HELPERS_LOCK_CHECK=1` — exits 0 without checking, for the rare disconnected dev machine.

The hook is the first line of defense against forgotten rebuilds; deploy.sh's drift guard is the second.

## First-time Bootstrap

The lock is committed to git, but its tagged images do not exist in a fresh Harbor. Bootstrapping a new install therefore requires:

1. `bash scripts/install-tools.sh` (existing).
2. `sudo bash scripts/setup-k3s.sh` (existing, requires sudo).
3. `bash scripts/deploy.sh` — first round, brings up Harbor + monitoring + control-plane services. The backend Deployment will not yet have helper images; it crashes early on `validate_helper_images` (or, if `BUILD_IMAGE_HELPER` was supplied via `--set` to a missing tag, the pod fails to pull). This is intentional; the operator sees the failure and proceeds.
4. `bash scripts/recover-harbor.sh` — creates the `lolday` Harbor project, the robot account, and the `harbor-push-cred` Secret in the `lolday` namespace.
5. `bash scripts/build-helpers.sh` — pushes the helper images for the SHAs already pinned in the committed lock.
6. `bash scripts/deploy.sh` — second round; backend now boots clean.

`README.md` records this order. `docs/runbooks/deploy.md` §5 cross-references it.

## Documentation Updates

### New: `docs/runbooks/release-helpers.md`

Sections:

1. **What this is** — content-addressable helper image release; `scripts/build-helpers.sh` is the only entrypoint.
2. **Pre-requisites** — host docker, kubectl context pointing at server30, `harbor-push-cred` Secret already created by `scripts/recover-harbor.sh`.
3. **Standard flow.**
   - Edit `charts/lolday/helpers/<name>/...`.
   - `cd backend && uv run pytest charts/lolday/helpers/<name>/...` for any per-helper unit tests (build-helper has them; job-helper has its own `tests/`).
   - `git commit` the source change (the dirty-tree guard requires it).
   - `bash scripts/build-helpers.sh` — computes SHAs, builds idempotently, pushes, writes the lock.
   - `git diff charts/lolday/helpers.lock` to confirm.
   - `git commit charts/lolday/helpers.lock -m "chore(helpers): rebuild <name> at <sha>"`.
   - `bash scripts/deploy.sh`.
4. **Variants** — `--dry-run`, `--only NAME`, `--allow-dirty` (and the rule against using `--allow-dirty` for production).
5. **Rollback** — `git revert` the lock commit, redeploy. Old SHA tags persist in Harbor (Harbor does not auto-prune), so rollback is a redeploy, not a rebuild.
6. **CI integration sketch** — workflow that runs `bash scripts/build-helpers.sh && git diff --exit-code charts/lolday/helpers.lock`; a non-zero diff triggers `gh pr edit` to commit the lock and request review.

### Modified files

| File                               | Change                                                                                                                                                 |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `README.md`                        | Setup section gains step 5 (`bash scripts/build-helpers.sh`) before the second `bash scripts/deploy.sh` round.                                         |
| `CLAUDE.md` (root)                 | Quickstart commands gain `bash scripts/build-helpers.sh # build + push helper images`.                                                                 |
| `docs/runbooks/deploy.md`          | §5 cross-references the bootstrap order; explicitly notes the lock + drift-guard exit codes.                                                           |
| `docs/architecture.md`             | §9 #4 marked **resolved 2026-04-29** (helper image build automation); §9 #8 marked **resolved 2026-04-29** (versioning strategy).                      |
| `.claude/rules/charts-and-helm.md` | Helper images section rewritten: tags are SHAs from `helpers.lock`, never hardcoded; mlflow-server / pytorch-cu12-base remain on manual semantic tags. |
| `.claude/rules/scripts-and-ops.md` | Inventory gains `build-helpers.sh` (release category); document the dirty-tree rule and `--allow-dirty` etiquette.                                     |

## Risks, Edge Cases, and Mitigations

### Risk register

| Risk                                                             | Likelihood                       | Impact                                                             | Mitigation                                                                                                                                                                                                      |
| ---------------------------------------------------------------- | -------------------------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Operator skips `build-helpers.sh` after editing a helper subtree | Medium                           | Deploy fails at drift-guard or backend pod fails fast              | Pre-commit hook catches the change at commit; deploy.sh is the second line                                                                                                                                      |
| `harbor-push-cred` Secret missing on a new clone                 | Medium                           | `build-helpers.sh` exits 1                                         | Friendly error message names `recover-harbor.sh` as the prerequisite                                                                                                                                            |
| Two operators push the same SHA concurrently                     | Low                              | Harbor accepts both manifests under the same tag; last writer wins | Tag content addresses content; if the build is reproducible enough the contents agree, otherwise the later operator's manifest replaces the earlier's atomically — both are valid since the source is identical |
| `--allow-dirty` tag accidentally pushed to production            | Low–Medium                       | Pod runs unreproducible image                                      | Lock never records `-dirty` tags; `--allow-dirty` requires explicit opt-in; runbook flags the rule                                                                                                              |
| Harbor `:v3` / `:v4` tags lingering during migration             | Certain (until manual cleanup)   | Disk usage; potential confusion                                    | Harbor preserves both old and new tags side by side; legacy tag removal is deferred manual cleanup, called out in the runbook                                                                                   |
| Pre-commit hook trips on disconnected dev machine                | Low                              | Cannot commit                                                      | `LOLDAY_SKIP_HELPERS_LOCK_CHECK=1` env escape hatch documented in the rules                                                                                                                                     |
| Shallow clone (CI default)                                       | Medium for CI, low for human ops | `git rev-parse HEAD:<path>` fails                                  | Explicit pre-flight check prints `git fetch --unshallow`                                                                                                                                                        |
| Lock JSON malformed by hand-edit                                 | Low                              | deploy.sh python parse blows up                                    | `python3 -c json.load` propagates; runbook tells operators not to hand-edit                                                                                                                                     |
| Subtree includes `__pycache__` or other untracked directories    | N/A                              | None                                                               | `git rev-parse HEAD:<path>` reads only committed contents; ignored files do not affect the SHA                                                                                                                  |

### Edge cases

- **Single-helper edit.** `--only NAME` updates that key in the lock without touching the other; the other key keeps its existing image ref.
- **No-op rebuild.** Two consecutive `build-helpers.sh` runs on a clean tree: the first is idempotent (skip via Harbor API); the second computes the same SHAs, sees them in Harbor, prints `[skip]` for both, and reports `[lock] unchanged`.
- **Unicode in helper paths.** Not supported; helper directory names stay ASCII (today: `build-helper`, `job-helper`).
- **Tests that read `Settings`.** Existing test fixtures rely on `ENVIRONMENT=development` (the `validate_sso_config` precedent); the new `validate_helper_images` follows the same gate, so test fixtures keep passing without modification.

## Testing

| Surface                              | Mechanism                                                                                                     | Cases                                                                                                                                                                                                           |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/build-helpers.sh` behaviour | `tests/build-helpers/` shell suite, mirroring `tests/phase7/` style                                           | dry-run prints expected SHAs; clean second run skips both; dirty subtree exits 1; `--allow-dirty` produces a `-dirty-<ts>` tag and skips lock write; `--only NAME` only modifies one key; lock JSON round-trips |
| `Settings.validate_helper_images`    | `backend/tests/test_config.py`, mirroring the existing `validate_sso_config` test                             | empty value + production → `ValueError`; empty value + development → no raise; non-empty + production → no raise                                                                                                |
| `scripts/deploy.sh` drift guard      | Same shell suite, fixture lock with deliberately stale ref                                                    | exits 1 with the drift message                                                                                                                                                                                  |
| Helm template rendering              | `helm template charts/lolday > /dev/null` after the `values.yaml` deletions, with required `--set` injections | renders without error; without injections the backend pod ends up missing the env keys (validated by inspecting the rendered output)                                                                            |
| Pre-commit hook                      | Manual smoke at landing time                                                                                  | edit a helper Dockerfile without re-running build → hook blocks; `LOLDAY_SKIP_HELPERS_LOCK_CHECK=1` lets it through                                                                                             |

Tests deliberately do not include:

- Live Harbor push end-to-end. server30 is the only viable target; the human pre-flight in the runbook covers it.
- Helper image runtime behaviour. The build-helper and job-helper repos already carry their own pytest suites; image-shape changes do not affect their test surface.

## Acceptance Criteria

This phase is complete when:

1. `scripts/build-helpers.sh` exists, executable, with the contract above. Running it with no args on a clean tree pushes the two current-content helper images to Harbor (or skips if already there) and writes `charts/lolday/helpers.lock` matching the HEAD subtree SHAs.
2. `charts/lolday/helpers.lock` is committed to git with the two current helper image refs.
3. `backend/app/config.py` has empty defaults for `BUILD_IMAGE_HELPER` and `JOB_HELPER_IMAGE`, plus the `validate_helper_images` model_validator. `cd backend && uv run pytest` passes.
4. `charts/lolday/values.yaml` no longer contains `backend.env.BUILD_IMAGE_HELPER`, `backend.env.JOB_HELPER_IMAGE`, or `jobs.helperImage`. `helm lint charts/lolday` passes.
5. `scripts/deploy.sh` reads the lock, drift-guards, and injects via `--set`. A deploy with the lock present succeeds; a deploy with the lock missing or drifted exits 1.
6. `scripts/recover-harbor.sh` no longer references `build-helper:v2` / `job-helper:v2`; the recovery flow either invokes `scripts/build-helpers.sh` or prints the next-step instruction.
7. `.pre-commit-config.yaml` carries the `helpers-lock-fresh` hook; editing a helper subtree without rebuilding blocks `git commit`.
8. `docs/runbooks/release-helpers.md` exists. `README.md`, root `CLAUDE.md`, `docs/runbooks/deploy.md`, `docs/architecture.md` §9, `.claude/rules/charts-and-helm.md`, and `.claude/rules/scripts-and-ops.md` reflect the new flow.
9. A round-trip rehearsal — touch `charts/lolday/helpers/build-helper/maldet_validator.py`, commit, run `bash scripts/build-helpers.sh`, commit the new lock, run `bash scripts/deploy.sh` — succeeds end-to-end on server30.

## Migration Plan

The change lands in two reviewable commits to keep the running platform intact at every checkpoint.

### Commit 1 — `feat(helpers): introduce content-addressable build flow`

Files added:

- `scripts/build-helpers.sh`
- `scripts/check-helpers-lock.sh`
- `charts/lolday/helpers.lock` (filled with the SHAs of the current `build-helper` and `job-helper` subtrees)
- `charts/lolday/.helmignore` entry for `helpers.lock`
- `tests/build-helpers/` shell tests
- `.pre-commit-config.yaml` `helpers-lock-fresh` hook entry

Files unmodified at this point: `config.py`, `values.yaml`, `deploy.sh`, `recover-harbor.sh`. Existing `:v3` / `:v4` deploys keep working.

Side effect during commit-1 landing: the operator runs `bash scripts/build-helpers.sh` once locally to push the new SHA-tagged images to Harbor (alongside the still-pinned `:v3` / `:v4`). The PR description records the SHAs.

### Commit 2 — `feat(helpers): switch deploy to lock-pinned helper images`

Files modified:

- `backend/app/config.py` — defaults to empty + new validator
- `charts/lolday/values.yaml` — three deletions
- `scripts/deploy.sh` — drift guard + two `--set` lines
- `scripts/recover-harbor.sh` — drop helper lines, delegate
- `README.md`, `CLAUDE.md`, `docs/runbooks/deploy.md`, `docs/runbooks/release-helpers.md` (new), `docs/architecture.md`, `.claude/rules/charts-and-helm.md`, `.claude/rules/scripts-and-ops.md`

Tests modified: `backend/tests/test_config.py` gains `validate_helper_images` cases.

Landing this commit and running `bash scripts/deploy.sh` cuts production over to the SHA-tagged images. Backend pods restart on the new env values; helper images are pulled by the new SHAs (already in Harbor from commit 1).

### Optional follow-up (not part of this phase)

Manually delete the `:v3` / `:v4` Harbor tags via the Harbor UI or `harbor-inventory.sh`. Pure cleanup; the migration is complete without it.

## Open Questions

None. All design decisions are resolved.
