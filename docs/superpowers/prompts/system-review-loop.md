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

Run **all four layers** every iteration. Do not rely on the previous
round's result — new candidates appear between iterations (CI re-runs,
upstream advisories, freshly-merged PRs surface new §10 follow-ups).
Paste the literal command output for each layer into the conversation;
a layer that was not actually executed does not count.

1. **Tracked tech debt** — `docs/architecture.md` §tech-debt,
   `docs/superpowers/specs/*.md` §10 follow-ups, `docs/postmortems/*.md`
   open actions. Run `grep -n "§10\|tech.debt\|TODO\|follow-up" docs/`
   and enumerate matches.
2. **External signals** — `gh issue list --state open --limit 50`,
   open `gh pr list` review comments, Dependabot security advisories
   (`gh api /repos/{owner}/{repo}/dependabot/alerts`),
   Trivy / gitleaks / Kyverno audit warnings from the most recent CI runs.
3. **Proactive probing** — at minimum run, and paste a summary line for
   each:
   - `pre-commit run --all-files`
   - `cd backend && uv run pytest -q --maxfail=1`
   - `cd frontend && pnpm test`
   - `helm lint charts/lolday`
   - Trivy on the current image tags in `helpers.lock`
     Any non-zero exit or new warning is a candidate.
4. **Convention / mainstream-practice drift** — cross-check code, charts,
   and docs against CLAUDE.md mainstream-practice rules and OWASP / CIS /
   SLSA baselines; for any unfamiliar upstream behaviour use `context7` or
   web search before guessing (per CLAUDE.md "search before
   trial-and-error"). At minimum re-check one CLAUDE.md rule per round
   that has not been audited in the current session.

**Selection order (overrides raw ranking):**

1. If tier 1 (tracked tech debt) has any viable candidate this iteration,
   pick from tier 1 — even if a tier 3 / 4 doc fix would score higher on
   the impact-formula. The point of this loop is to retire tech debt,
   not to inflate the merge count with cosmetic changes.
2. If tier 1 is empty this iteration, pick from tier 2 (external
   signals) — failing CI, security advisories, review comments.
3. Only fall through to tier 3 / 4 (probing failures, drift, docs) when
   1–2 are both empty.

Within the chosen tier, rank by **user impact × likelihood of recurrence
÷ blast radius** and pick the top one. "Trivial" is not a disqualifier
inside its tier — docs typos, comment fixes, and lint tweaks remain
valid tier-4 candidates when 1–3 are empty.

## Process tier (decide per candidate)

**Fast-lane** — straight to PR, no spec / plan:

- docs / runbook / comment / typo edits
- lint config or pre-commit hook tweaks
- Dependabot bumps that pass CI cleanly
- single-file dead code / unused import removal
- Trivy fix-only base-image bumps with no behaviour change

**Session balance** — at most **1/3** of merged PRs in this run may be
docs-only (no behaviour change, no code edit outside `*.md`). Once the
quota is hit, skip docs-only candidates for the rest of the session and
keep working on tier 1–2 items. If tier 1–2 are also empty, that is a
signal to invoke the "Discovery exhausted" stop gate — not a license to
keep merging docs PRs.

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

Default posture: **keep going**. Each stop condition below is a hard
gate — the conditions in italics must all be true before you may exit on
that line. Vague feelings ("this seems low value", "I've done enough",
"I should wrap up") are NOT stop conditions; ignore them and start the
next iteration.

- 15 PRs merged this run (hard upper bound)
- Hit any item in the "stop and hand back" authorisation list above
- Same PR CI fails twice for non-flaky reasons
- **Discovery exhausted** — _all of:_ (a) at least 5 PRs already merged
  in this run, (b) 3 consecutive iterations turned up zero candidates,
  (c) for each of those 3 iterations the literal output of all four
  discovery layers is pasted into the conversation. A round where any
  layer was skipped, summarised from memory, or replaced with "looks
  fine" does NOT count toward (b).
- **Context budget exhausted** — _only if_ you receive an explicit
  system warning about context limits, OR conversation context
  compaction has already happened in this session. Subjective fatigue
  ("I'd rather skim than read", "this is getting long") does NOT qualify.

If none of the above hard gates is met, you MUST start the next
iteration, even if the next candidate feels small.

## Handoff for next session (only output at end)

Write a `## Next session` block containing:

- **Stop reason** — name the exact stop condition that fired
  (e.g. "Discovery exhausted", "15 PRs reached", "stop-and-hand-back:
  sudo needed for X"). If "Discovery exhausted", paste the proof: the
  four discovery-layer outputs from the final empty iteration.
- Any `sudo` command I need to run, exact and copy-pasteable
- Any PR opened but not merged, with the blocker reason and PR link
- Candidates intentionally deferred (and why) — one line each: title +
  file path / issue link + reason for deferring
- One sentence the next session can paste at the start of the prompt to
  resume cleanly

No merge log — `gh pr list --search "merged:>YYYY-MM-DD author:@me"` covers
that.

Start now.
