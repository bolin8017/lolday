---
paths:
  - "scripts/**"
  - "*.sh"
---

# Scripts & ops rules

## Script categories (live inventory in `scripts/`)

- **Install / deploy** — `install-tools.sh` (CLI tools to `~/.local/bin/`, no sudo), `setup-k3s.sh` (sudo-required, hand to operator), `deploy.sh` (Helm dep update + upgrade --install), `teardown.sh`.
- **Diagnostics** — `diag-backend-401.sh`, `diag-pv-data.sh`, `disk-diag.sh`, `find-lost-data.sh`.
- **Recovery** — `recover-harbor.sh`, `harbor-inventory.sh`, `fix-lolday-project-public.sh`, `patch-k3s-registries.sh`.
- **Data migration (Phase 8.2 / 9.6)** — `migrate-ephemeral-to-ssd.sh`, `migrate-all-root-pvcs.sh`, `cleanup-migrated-shelves.sh`.
- **Phase pre-checks** — `phase4-pre-deploy-check.sh`, `phase6-pre-deploy-check.sh`. Templates for future phases that touch deploy.
- **One-shot Python** — `backfill-summary-metrics.py`, `sample_elf_dataset.py`.

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

## Engineering hygiene 紀律

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
