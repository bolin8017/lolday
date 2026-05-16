---
paths:
  - "scripts/**"
  - "*.sh"
---

# Scripts & ops rules

## Script categories (live inventory in `scripts/`)

- **Install / deploy** — `install-tools.sh` (CLI tools incl. cosign to `~/.local/bin/`, no sudo; also unsets a redundant `core.hooksPath` that would shadow pre-commit), `setup-k3s.sh` (sudo-required; bakes in kube-apiserver audit + `--secrets-encryption` flags on fresh installs), `deploy.sh` (Helm dep update + upgrade --install), `build-helpers.sh` (helper-image release: subtree SHA tag, idempotent push, cosign sign by digest, writes `charts/lolday/helpers.lock`; runbook `docs/runbooks/release-helpers.md`), `check-helpers-lock.sh` (drift guard used by pre-commit + deploy), `teardown.sh`.
- **Supply-chain bootstrap** — `cosign-harbor-init.sh` (one-time per cluster: generates `~/.cosign/lolday-harbor.{key,pub}`, installs the public half as `kyverno/cosign-harbor-pubkey`; `--force-new` for key rotation; runbook `docs/runbooks/kyverno-harbor-signing.md`).
- **Diagnostics** — `diag-backend-401.sh`, `diag-pv-data.sh`, `disk-diag.sh`, `find-lost-data.sh`.
- **Recovery** — `recover-harbor.sh`, `harbor-inventory.sh`, `fix-lolday-project-public.sh`, `patch-k3s-registries.sh`.
- **K3s host patches** — `patch-k3s-kubelet-args.sh` (kubelet reservations on an existing cluster), `patch-k3s-audit-and-secrets-encryption.sh` (apply kube-apiserver audit log + `--secrets-encryption` to an existing cluster; sudo + step-by-step `read -r -p` confirmation; runbook `docs/runbooks/k3s-audit-and-secrets-encryption.md`).
- **Data migration (Phase 8.2 / 9.6 / 2026-05-11 MinIO)** — `migrate-ephemeral-to-ssd.sh`, `migrate-all-root-pvcs.sh`, `cleanup-migrated-shelves.sh`, `migrate-harbor-to-s3.sh`, `migrate-mlflow-to-s3.sh`, `migrate-jobs-namespace.sh`, `migrate-mlflow-experiment-naming.sh`, `storage-audit.sh`, `rotate-minio-keys.sh`, `validate-add-ssd-runbook.sh`.
- **Phase pre-checks** — `phase4-pre-deploy-check.sh`, `phase6-pre-deploy-check.sh`. Templates for future phases that touch deploy.
- **One-shot Python** — `backfill-summary-metrics.py`, `sample_elf_dataset.py`.
- **One-shot user-facing-NP guard** — `check-user-facing-np.sh`, `check-image-tags-aligned.sh` (pre-commit-style guards used by `.pre-commit-config.yaml`).

## Sudo discipline

The operator has no sudo by default. Sudo is granted temporarily and then revoked.

- Never `set -euo pipefail` and then run the whole script under sudo. Wrap individual sudo lines and comment them `# requires sudo`.
- If the script genuinely needs sudo end-to-end, echo a banner at the top and abort if `[ "$(id -u)" != 0 ]`.
- Prefer writing what would be sudo'd as a separate snippet for the operator to run, instead of invoking sudo directly inside the script.

## SSH discipline (covered by hard rule, with operational specifics)

For any iptables / ufw / cilium / k3s flannel / sysctl change:

1. Print the proposed change to stdout (dry run).
2. Pause: prompt the operator to verify SSH from a fresh session.
3. Apply only after operator confirmation.

After any infra step, prompt the operator to re-verify SSH on port 9453 from outside the host. See `docs/postmortems/2026-03-31-cilium-ssh-incident.md`.

## Secrets path fallback pattern

Scripts that source `.lolday-secrets.env` should follow `recover-harbor.sh` / `harbor-inventory.sh`'s pattern (repo-root preferred, `~/` as fallback):

```bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS=${SECRETS:-${REPO_ROOT}/.lolday-secrets.env}
[ -f "$SECRETS" ] || SECRETS="$HOME/.lolday-secrets.env"
[ -f "$SECRETS" ] || { echo "secrets file not found" >&2; exit 1; }
# shellcheck disable=SC1090
source "$SECRETS"
```

All scripts that source `.lolday-secrets.env` follow this pattern as of 2026-04-29: `recover-harbor.sh`, `harbor-inventory.sh`, `fix-lolday-project-public.sh`, and (in spirit, with custom root-execution logic) `diag-backend-401.sh`. `phase6-pre-deploy-check.sh` doesn't source the file — it just verifies env vars are already exported — and its error messages now name `.lolday-secrets.env` without prejudging the location.

## Writing a new script

```bash
#!/usr/bin/env bash
set -euo pipefail

# Required env (fails early if missing)
: "${VAR:?VAR is required}"

echo "[step 1] doing thing..."
# work
echo "[step 2] verifying..."
# verify
```

- shebang `#!/usr/bin/env bash`
- `set -euo pipefail` at the top
- `${VAR:?required}` expansion for mandatory env
- `[step N] ...` echo-format logs to stdout, errors to stderr (`>&2`)
- Idempotent where possible

## Engineering hygiene discipline

Repo-wide formatting / linting / type-check is governed by `pre-commit`. Config is at repo root (`.pre-commit-config.yaml`); install + activation happens in `scripts/install-tools.sh`.

Repo-wide manual commands:

```bash
pre-commit run --all-files            # run every hook over the entire repo
pre-commit run <hook-id> --all-files  # run a single hook (e.g. ruff, prettier, mypy)
pre-commit autoupdate                 # bump hook revs (optional, ~quarterly)
pre-commit install                    # re-activate the git hook (idempotent)
```

### Forbidden

- `git commit --no-verify` — bypasses the hook. If a hook fails, fix the root cause; do not bypass.
- `|| true` inside hook scripts — failures must surface.
- New `.py` scripts must conform to the root `ruff.toml`. Shell scripts are not linted by ruff (non-Python); shellcheck is out of scope for this phase.

## Phase pre-deploy checks

`phase4-pre-deploy-check.sh` and `phase6-pre-deploy-check.sh` exist as templates. New phases that touch deploy should add an analogous pre-check (verify required env is set, required PVCs exist, required CRDs installed, etc.). Avoid one-off checklists in markdown — code is more reliable.

## Helper image release discipline

`scripts/build-helpers.sh` is the only sanctioned way to push `build-helper` and `job-helper` images. Tags are content-addressable (12-char subtree SHA from `git rev-parse HEAD:<path>`) and pinned in `charts/lolday/helpers.lock`.

### Forbidden

- Hardcoding helper image refs in `backend/app/config.py`, `charts/lolday/values.yaml`, or anywhere else. The lock is the only source of truth.
- Pushing a `-dirty-<ts>` tag from `--allow-dirty` to a production deploy. The lock never records dirty tags; using one in `helm upgrade --set ...` is a deliberate operator override and must be justified in the deploy log.

### Rules

- The dirty-tree refusal in `build-helpers.sh` is intentional. To iterate on uncommitted changes, pass `--allow-dirty` knowingly — the runbook covers the rule.
- The pre-commit `helpers-lock-fresh` hook fails on drift. Override only with `LOLDAY_SKIP_HELPERS_LOCK_CHECK=1` and only when the build itself cannot run (no docker, no kubectl); otherwise fix the root cause by re-running `build-helpers.sh`.
- `scripts/recover-harbor.sh` no longer rebuilds helper images directly. It tail-calls `build-helpers.sh` if the lock exists; otherwise it points the operator to run it manually.
- Adding a new helper means: edit the `HELPERS=(...)` array in `build-helpers.sh`, edit the JSON keys in `helpers.lock` and the script's `write_lock` body, add the corresponding `--set` line in `deploy.sh`, and document in `docs/runbooks/release-helpers.md`.

## CI

Engineering-hygiene scripts (pre-commit, install-tools.sh) are mirrored on every PR by `.github/workflows/lint.yml`. Discipline rules in `.claude/rules/github-actions.md`.

## R6 — Touched script must add lib + test

Phase 4 D4.5. When a PR modifies any script under `scripts/`:

- **Pure shell changes** (renaming, refactoring shell control flow, fixing a flag-parse bug): add or extend a `tests/bats/<script>_smoke.bats` case that covers the changed path. The bats workflow (`.github/workflows/bats.yml`) runs every `.bats` file in CI.
- **Embedded `python3 -<<'PY' ... PY` heredoc changes**: do NOT modify in place. Extract the heredoc into a `scripts/lib/<topic>.py` module (named by the area it serves — `harbor_api`, `helpers_lock`, etc.), invoke from bash via `python3 -m scripts.lib.<topic> <verb>`, and add a pytest unit at `scripts/tests/lib/test_<topic>.py`. `backend-fast.yml` runs `uv run pytest ../scripts/tests/lib/` as part of its existing pytest invocation.
- **New scripts**: ship with both a bats smoke and (if the script does non-trivial Python or HTTP work) a pytest unit from day 1.

The rationale (R6 in `docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md` §9): PR #184 (Python heredoc bug in `build-helpers.sh`) and PR #155 (apostrophe escape in `recover-harbor.sh`) both shipped without a test gate — bats + pytest would have caught them. The cost of the test is linear in the change size; the cost of a regression is unbounded.

Existing `scripts/lib/` modules (the extraction precedents):

- `scripts/lib/flaky_aggregate.py` (Phase 1 D1.13) — JUnit XML → flaky-issue opener.
- `scripts/lib/harbor_api.py` (Phase 4 D4.2) — Harbor v2 REST helpers used by `build-helpers.sh` + `recover-harbor.sh`.
- `scripts/lib/helpers_lock.py` (Phase 4 D4.2) — `charts/lolday/helpers.lock` read/write/drift-check.
- `scripts/lib/mutation_report.py` (Phase 4 D4.3) — mutmut state → Markdown report.
- `scripts/lib/test_telemetry.py` (Phase 4 D4.4) — JUnit XML → dashboard + Discord summary.
