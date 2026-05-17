You are running an open-ended autonomous improvement loop on the lolday
repository. Each iteration: discover the next highest-value issue, ship the
fix end-to-end, then start the next iteration. Continue until a stop
condition triggers, then output a handoff note for the next session.

Project context (CLAUDE.md, `.claude/rules/`, `docs/architecture.md`,
`docs/conventions.md`, auto-memory) loads automatically at session start —
trust it, do not re-discover.

## Iteration init (run once at start)

- Confirm `git status` clean and on `main` synced to `origin/main`
- `gh auth status` OK; otherwise stop with a handoff
- Skim `docs/superpowers/specs/` and `docs/architecture.md` §tech-debt for
  newly-added items since last run

## Authorization

You may run autonomously:

- All read operations
- File edits, tests, `kubectl get|logs|describe|exec`, `docker`,
  `helm lint`, `helm template`, `pre-commit run`
- `git commit`, `git push`, `gh pr create`, `gh pr edit`,
  `gh pr merge --squash --delete-branch`, fixing CI failures
- `kubectl exec` into backend pod to verify API behaviour

You MUST stop and hand back (write the exact command into the handoff note,
do not execute):

- Any `sudo` command
- SSH / iptables / UFW / CNI / sysctl / fstab changes
- Force-push, `git reset --hard`, deleting any branch other than the merged
  PR branch, bypassing `main` branch protection
- Secret rotation (Discord webhooks, Fernet, Harbor robot tokens, cosign
  keys, CF Access tokens)
- `git commit --no-verify`, `# noqa` / `# type: ignore` without a same-line
  reason, `# fmt: off` without a reason
- MLflow wipe, PG restore, Harbor blob delete, any production-data
  destructive action
- Adding China-origin packages, flipping storage back to filesystem, adding
  UI knobs that override detector-author design decisions

## Discovery (each iteration, in this priority order)

1. **Tracked tech debt** — `docs/architecture.md` §tech-debt,
   `docs/superpowers/specs/*.md` §10 follow-ups, `docs/postmortems/*.md`
   open actions
2. **External signals** — `gh issue list --state open`, open `gh pr list`
   review comments, Dependabot security advisories, Trivy / gitleaks /
   Kyverno audit warnings from the most recent CI runs
3. **Proactive probing** — `pre-commit run --all-files`,
   `cd backend && uv run pytest --maxfail=1`, `cd frontend && pnpm test`,
   `helm lint charts/lolday`, Trivy on current image tags; surface failures
   not yet tracked
4. **Convention / mainstream-practice drift** — cross-check code, charts,
   and docs against CLAUDE.md mainstream-practice rules and OWASP / CIS /
   SLSA baselines; for any unfamiliar upstream behaviour use `context7` or
   web search before guessing (per CLAUDE.md "search before
   trial-and-error")

Rank candidates by **user impact × likelihood of recurrence ÷ blast
radius**. Pick the top one.

## Process tier (decide per candidate)

**Fast-lane** — straight to PR, no spec / plan:

- docs / runbook / comment / typo edits
- lint config or pre-commit hook tweaks
- Dependabot bumps that pass CI cleanly
- single-file dead code / unused import removal
- Trivy fix-only base-image bumps with no behaviour change

**Spec-lane** — full brainstorming → spec in
`docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` → plan in
`docs/superpowers/plans/` → TDD → PR:

- schema or Alembic migration changes
- chart, Helm values, NetworkPolicy, Kyverno policy changes
- new endpoint / new module / cross-module interface change
- anything touching prod data / auth / RBAC / secrets handling
- anything reverting or changing a documented mainstream-practice choice

Follow project conventions exactly — branch naming (`feat/`, `fix/`,
`docs/`, `chore/`), conventional-commit messages, PR title format matching
recent merges, path-scoped `.claude/rules/<area>.md` when editing that
area.

## PR mechanics

- One fix per PR (matches recent lolday cadence; smaller PRs merge faster
  per DORA)
- Branch off latest `origin/main`; push; `gh pr create`; wait for all
  required check contexts to pass; `gh pr merge --squash --delete-branch`
- If CI fails: read the failure, fix the root cause (no `--no-verify`, no
  skipping tests), push the fix, re-wait
- If CI fails twice on the same PR and the cause is not a known flaky test:
  stop and hand back

## Stop conditions (any one triggers exit)

- 6 PRs merged this run
- 2 consecutive discovery rounds find no non-trivial candidate
- Hit any item in the "stop and hand back" authorisation list above
- Same PR CI fails twice for non-flaky reasons
- Context pressure: you start having to skim files you would normally read
  in full, or you cannot keep recent file contents in working memory

## Handoff for next session (only output at end)

Write a `## Next session` block containing only:

- Any `sudo` command I need to run, exact and copy-pasteable
- Next 1–3 candidates identified but not finished (one line each: title +
  file path / issue link)
- Any PR opened but not merged, with the blocker reason and PR link
- One sentence the next session can paste at the start of the prompt to
  resume cleanly

No merge log — `gh pr list --search "merged:>YYYY-MM-DD author:@me"` covers
that.

Start now.
