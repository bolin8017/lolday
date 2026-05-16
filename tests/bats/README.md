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
