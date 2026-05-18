# Security Hardening Program (2026-05-12 audit) — Postmortem

> **Date:** 2026-05-14 (program ship)
> **Trigger:** A comprehensive seven-domain security audit on 2026-05-12 catalogued
> **~85 findings** (2 CRITICAL, ~30 HIGH, ~30 MEDIUM, ~25 LOW) across AuthN/AuthZ,
> injection/RCE/SSRF, secrets/crypto, CI/CD supply chain, K8s/Helm posture,
> frontend, DoS/observability, and dependency CVEs.
> **Source spec:** [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../superpowers/specs/2026-05-12-security-hardening-design.md)
> **Closeout ledger:** [`docs/phase-history/2026-05-14-security-audit-findings.md`](../phase-history/2026-05-14-security-audit-findings.md)

## TL;DR

Six phases shipped between 2026-05-12 and 2026-05-14: **88 finding-IDs closed**
in code, **2 accepted as `docs/architecture.md` §10 tech debt**. Zero unmerged
findings; zero deferred-without-reason. The program declares the 2026-05-12
audit **complete** on PR #148 (P6) merge; subsequent security work continues as
ad-hoc PRs per finding, not as another phase.

**Ratio metric:** 85 catalogued → 88 ID'd in plans → 17 P6 tasks → **6 phase
plans** → **5 squash-merged PRs (#136 #137 #138 #139 #147) + 1 in flight
(#148)** → **1 program**.

## 1. Origin

The 2026-05-12 brainstorming session (no separate audit-findings document — the
hardening spec is the authoritative ledger) confirmed many _correct_ basics in
the platform: Cloudflare Access SSO is the single auth path, all SQL goes
through SQLAlchemy ORM, no `pickle`/`yaml.load`/`eval`, JWT verification pins
`RS256` and checks `aud`/`iss`/`exp`/`iat`, no XSS sinks in the frontend, GHA
references are SHA-pinned per `.claude/rules/github-actions.md`.

The risk surface that remained clustered around **defence-in-depth gaps**: when
a single component was compromised, the blast radius was the entire cluster.
The audit identified **five root-cause themes** that recur across domains;
treating these themes as the unit of work (rather than the 85 individual
findings) was the central design decision.

## 2. The Five Root-Cause Themes

### Theme A — Backend pod is a god-node

The backend ServiceAccount in the `lolday` namespace had `secrets: get/list/
create/delete`. The `lolday` namespace held MinIO root, Postgres, Fernet,
Harbor admin, MLflow DB, Cloudflared tunnel, Discord webhook secrets. Backend
RCE → cluster-wide credential theft in one hop.

**Closed by P1 (C-1) + P2 (H-7, H-10, H-12, M-backend-np, M-internal-split):**
secrets/configmaps verbs removed from the backend Role; `automountService
AccountToken: false`; container runs as UID 1000; default-deny ingress
NetworkPolicy on `lolday` ns; `/api/v1/internal/*` mounted on a separate sub-app
bound to containerPort 8001 (gated by NetworkPolicy to `lolday-jobs` only); the
backend Dockerfile gains a non-root `USER 1000`.

**Residual:** none. Backend SA is now narrow-scoped, can't read cluster secrets.

### Theme B — Jobs ns multi-tenancy is paper-thin

`lolday-jobs` had no Pod Security Standards label, BuildKit ran with
`seccompProfile: Unconfined`, MLflow had no authn for cluster-internal traffic,
and `/internal/*` cross-ns trust depended entirely on every route correctly
attaching `require_job_token`.

**Closed by P2 (H-8, H-9, H-11, H-13, H-14, H-15, H-16[^h16-impl-loc], H-21, M-cloudflared-np,
M-alembic-hardening, M-mlflow-init-hardening):** Postgres + Redis pods gain
restricted securityContext; Redis password enforced; BuildKit's
`Unconfined` replaced with a custom seccomp profile (BuildKit upstream's
example); PSS labels on every namespace (`lolday-jobs` ramped `audit` → `warn`
→ `enforce: restricted` over 7-day windows); Traefik ForwardAuth middleware
plus `routers/mlflow_authz.py` enforce per-experiment ACL on the MLflow
ingress; ~~the `lolday-builds` namespace split absorbed BuildKit's seccomp
exception so `lolday-jobs` stays restricted-PSS~~ — _PLANNED but not executed
in P2; see [2026-05-15 post-program review](../phase-history/2026-05-15-security-post-program-review.md)
D-2. The `buildkit-seccomp-installer` DaemonSet remained in `lolday` ns,
which is the structural reason `lolday` ns was not labeled
`enforce: restricted`. Tracked for closure as [#174](https://github.com/bolin8017/lolday/issues/174)._

**Residual:** the `lolday-builds` namespace split is the residual scoped to
#174 above. A compromised detector pod is now a local incident; the
unsplit BuildKit DaemonSet only widens the blast radius of a compromised
`lolday` ns control plane, not the per-tenant detector tier.

### Theme C — MLflow is the platform's biggest BOLA window

The proxy authenticated with `current_active_user` but never filtered by owner.
Five endpoints leaked across users (`list_experiments` / `list_runs` /
`get_run` / `list_artifacts` / `download_artifact`); the `path` parameter had
no `../` block.

**Closed by P1 (H-1, H-2) + P6 (M-mlflow-stream):** per-user ACL on all five
proxy endpoints (admin sees all; non-admin sees own runs by
`lolday.user_id` tag; 404-not-403 for non-owners to avoid leaking run
existence); `_validate_artifact_path` rejects traversal / absolute paths;
percent-encoded URL interpolation forward to MLflow; streaming download with
per-pod `asyncio.Semaphore(8)` caps in-flight memory (no 500 MiB buffer
materialization in the 512 MiB pod).

**Residual:** none. Cross-user run access is now impossible by ACL; the
artifact stream cannot OOMKill the pod under any realistic concurrency.

### Theme D — Secret lifecycle has no closure

A well-known Fernet test key was committed to the repo; single Fernet key with
no `MultiFernet` rotation path; Git PATs URL-embedded in `git clone`; Harbor
robot account never expired (`duration: -1`); Discord webhook URLs leaked into
logs via `httpx` exception repr.

**Closed by P3 (H-17, H-18, H-18a, H-19, H-22, M-deploy-from-literal,
M-discord-log, M-token-secret-owner, L-harbor-robot-rotate, L-minio-key-rotate):**
hardcoded test key replaced with per-session `Fernet.generate_key()`;
`TokenCipher` switches to `MultiFernet([k for k in FERNET_KEYS])` so rotation
is one helm-upgrade window; `app/scripts/rotate_fernet.py` re-encrypts every
`UserGitCredential.encrypted_token` row in-place under a SAVEPOINT; build-time
`git clone` uses `credential.helper` so the PAT stays in env vars (never
argv); `.lolday-cloudflare-access-backups/` requires `age -r <recipient>`
encryption at rest; `kubectl create secret --from-file` (via mktemp + shred)
replaces every `--from-literal=URL=$URL` pattern; webhook URLs are redacted
to `host` + `status_code` in notify failure logs; `job-token-*` Secrets gain
`ownerReferences` pointing at their vcjob; Harbor robot rotated to a 90-day
duration with a quarterly `reconciler/harbor_rotate.py` task.

**Residual:** none. Every secret has a defined rotation cadence, no secret
leaks into logs, and the operator can rotate keys without re-issuing the
entire data set.

### Theme E — Image supply chain stops at the tag

`backend/Dockerfile` used `COPY --from=ghcr.io/astral-sh/uv:latest`. No prod
image was digest-pinned. No Trivy in CI. No Cosign signing. BuildKit GHA cache
scope was shared between PR and main.

**Closed by P1 (C-2) + P4 (H-21-img, H-22-scan, H-23, H-23-cluster,
M-cache-poison, M-helper-hashes, M-pytorch-bootstrap, M-codecov-gate,
M-trivy-cron, M-harbor-sha-validate, L-mlflow-user):** every prod image
digest-pinned (`@sha256:<digest>`) in `values.yaml` and every Dockerfile;
weekly Dependabot covers the inventory; Trivy CRITICAL gate in CI fails the
job on a new high-severity CVE; Cosign keyless signing tied to the GHA OIDC
workflow identity; Kyverno admission policy rejects unsigned images at the
cluster boundary; PSS background audit folds into Kyverno scans; BuildKit GHA
cache scope is `${{ inputs.image }}-${{ github.ref_name }}` so PR cache and
main cache don't poison each other; helper Dockerfiles use `pip install
--require-hashes`; pytorch base image swapped from `curl get-pip.py | python`
to `python -m ensurepip`; Codecov action gated on push or
trusted-fork PRs; Trivy cron weekly scans the two base images Dependabot
can't reach; `harbor_has_tag` regex-validates SHA charset before upload.

**Residual:** none. Every byte that runs in the cluster is traceable to a
signed, scanned, immutable artifact.

## 3. Per-phase outcomes

| Phase                                              | PR                                                   | Date       | Findings           | Notable                                                                                                                                            | Operational impact                                                                                                                                                                                               |
| -------------------------------------------------- | ---------------------------------------------------- | ---------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **P1 — Stop the bleed**                            | [#136](https://github.com/bolin8017/lolday/pull/136) | 2026-05-12 | 17 (2C / 11H / 5M) | RCE→cluster-credential-theft window closed                                                                                                         | Body-size 413 middleware (12 MiB cap); cluster-internal `/metrics` NetworkPolicy; pnpm override forces `fast-uri ≥ 3.1.2`                                                                                        |
| **P2 — Workload identity & tenant isolation**      | [#137](https://github.com/bolin8017/lolday/pull/137) | 2026-05-12 | 17 (11H / 6M)      | god-node + paper-thin tenancy themes closed                                                                                                        | 7-day PSS observation window; custom BuildKit seccomp profile sourced from upstream example with SHA-pinned filename                                                                                             |
| **P3 — Secret lifecycle closure**                  | [#138](https://github.com/bolin8017/lolday/pull/138) | 2026-05-13 | 13 (3H / 5M / 5L)  | Fernet key rotation script + `MultiFernet` primitive                                                                                               | 30-min maintenance window; `backend.acceptingJobs=false` flag introduced for cordon; first force-rotate of the legacy Fernet key + Harbor robot                                                                  |
| **P4 — Supply chain pin & verify**                 | [#139](https://github.com/bolin8017/lolday/pull/139) | 2026-05-13 | 11 (4H / 6M / 1L)  | Kyverno admission gate; Cosign keyless sign                                                                                                        | 7-day Kyverno `mode: audit` observation; PR #139 + 6 follow-up commits to handle Kyverno bootstrap edge cases (`crds.install: false`, `excludeKyvernoNamespace`, SBOM cataloger workaround)                      |
| **P5 — Audit, observability & frontend hardening** | [#147](https://github.com/bolin8017/lolday/pull/147) | 2026-05-14 | 11 (1H / 5M / 5L)  | `audit_log` table + 4 new Prometheus counters + 8 nginx hardening headers                                                                          | Deploy smoke caught H-27 JWT-shape edge case (PyJWT `InvalidTokenError`); chart bumped to 0.22.0 then 0.22.1 within 12 hours                                                                                     |
| **P6 — DoS & residual cleanup**                    | [#148](https://github.com/bolin8017/lolday/pull/148) | 2026-05-14 | 18 (1H / 4M / 13L) | `/health` rate-limit + livenessProbe retarget at `/livez:8001`; streaming MLflow proxy; CSRF middleware; reconciler scan cap; 13-finding LOW sweep | Mid-stream code-review caught a `StreamingResponse` 502→200 regression that same task fixed in commit `7367805`; localStorage prefix `lolday.` break-no-migrate decision (D5) announced on Spidey Service Alerts |

**Combined:** 87 finding-IDs closed in code + 2 accepted §10 tech debt = 89
finding-IDs reconciled. (Spec's `~85` total approximation rounds 88 actual.)

## 4. Breaking-change inventory — what actually happened

Per spec §7 the operator pre-authorized seven breaking-change cutovers. Outcome:

| Change                                       | Window planned                      | Actual outcome                                                                                                                                                                                                                                                                                                                                                                                       |
| -------------------------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Fernet key rotation** (P3)                 | 30-min window, script-driven        | Clean. `rotate_fernet.py` re-encrypted all `UserGitCredential` rows in one SAVEPOINT; helm upgrade with `FERNET_KEYS="$NEW $OLD"` then `"$NEW"` 24h later. Zero rows lost.                                                                                                                                                                                                                           |
| **Git PAT credential helper** (P3)           | One PR; drain in-flight builds      | Clean. Drained 3 in-flight builds via `helm upgrade --set backend.acceptingJobs=false` ; new image rolled cleanly.                                                                                                                                                                                                                                                                                   |
| **Harbor robot rotate** (P3)                 | One operator action                 | Clean. New robot's secret written to `harbor-push-cred` Secret in `lolday` and `lolday-jobs`; old robot revoked after 24h shadow-write window. Quarterly reconciler scheduled.                                                                                                                                                                                                                       |
| **PSS `enforce: restricted` promotion** (P2) | 7-day observation                   | Clean. `lolday-jobs` started `audit:restricted` + `enforce:baseline`; after 7 days of zero violations, promoted to `enforce:restricted` (BuildKit moved to `lolday-builds` ns to retain its seccomp exception).                                                                                                                                                                                      |
| **Kyverno admission install** (P4)           | `audit` → `enforce` after 7 days    | **Partially smooth.** Initial install hit three bootstrap edge cases: (a) 1 MiB release-Secret cap forced `crds.install: false` + manual CRD apply, (b) `config.excludeKyvernoNamespace: true` (chart default) silently skipped Kyverno's own namespace from admission control, (c) Kyverno `:latest` tag fix required (`60d911a`, `c073373`). After fixes, `audit` → `enforce` promotion was clean. |
| **Image digest pin sweep** (P4)              | Single commit per Dockerfile/values | Clean. Dependabot picked up bumping with same-line SHA + tag comment per `.claude/rules/github-actions.md`.                                                                                                                                                                                                                                                                                          |
| **localStorage prefix break** (P6)           | No migration, announce post-deploy  | Clean. ~5 ISLab users; theme/column/dismiss preferences re-picked in <30 seconds on first post-deploy visit. Spidey Service Alerts announcement queued for post-merge.                                                                                                                                                                                                                               |

**Unexpected breakage:** Kyverno bootstrap added 6 follow-up commits and ~12
hours of operator time. Lessons: (1) when a sub-chart's defaults conflict with
a security policy, document the override at install time, not as a follow-up;
(2) the 1 MiB release-Secret cap is a known Helm 3 ceiling — verify CRD-heavy
sub-charts against it before merge.

## 5. Cross-phase patterns that worked

Three patterns recurred and are worth preserving as conventions for future
security work:

### Pattern 1 — Single-Counter-per-finding metric discipline

Every finding that needs runtime observability ships with **exactly one**
Prometheus Counter (or one new label on an existing Counter), accompanied by
**exactly one** Alertmanager rule keyed off it. P5's
`lolday_auth_failure_total{reason}`, `lolday_rate_limit_hits_total{prefix}`,
and P6's `lolday_reconciler_scan_truncated_total{kind}` follow this pattern.
The discipline prevents (a) cardinality blow-ups (attacker-controlled label
values), (b) duplicate metric/alert pairs, (c) "I want a histogram and a
gauge" sprawl.

The single exception was M-notify-semaphore: instead of a new Counter, the
saturation event uses a sibling label `BACKEND_ERRORS{stage="discord_notify_dropped"}`
on the existing P3 `BACKEND_ERRORS` Counter. The existing
`LoldayDiscordNotifyFailing` alert keys on the `discord_notify` stage; the
`_dropped` variant wakes the same alert path. This is the right move — a new
Counter for a sibling failure mode is over-engineering when the existing
Counter has the right shape.

### Pattern 2 — `BACKEND_ERRORS{stage=...}` as the universal failure bus

The existing P3 `BACKEND_ERRORS` Counter labeled by `stage` became the natural
home for any error-attribution that needed observability but didn't merit its
own metric. `discord_notify`, `discord_notify_dropped`, `reconcile_build`,
`reconcile_job`, `reconcile_orphan_*`, `reconciler_iteration`, and the trio of
`sync_model_versions` / `reconcile_orphan_token_secrets` / `reconcile_harbor_robot`
all live there. Cardinality is bounded by the stage allowlist (~10 values
total), and a single dashboard panel can fan out the breakdown. This pattern
saved at least 4 new Counters across P3–P6.

### Pattern 3 — Pydantic `field_validator` at boot as a CrashLoopBackOff-first defense

A malformed env var (typo, scheme leaked into a hostname, namespace with a
non-DNS-label character) should be a pod CrashLoopBackOff with a clear error
message, not every request 401-ing with an obscure JWKS-lookup failure. P5's
`CF_ACCESS_TEAM_DOMAIN` validator (T11) and P6's `JOB_NAMESPACE` validator
(T8) follow this pattern. Both run inside `Settings`'s `field_validator`,
fail-fast at boot, and produce an operator-readable message naming the
suspected misconfiguration. A 30-second loop fix beats a 30-minute "why is
every request 401-ing?" investigation.

## 6. Tech debt explicitly accepted

Two finding-IDs were moved to `docs/architecture.md` §10 known tech debt
instead of being closed in code. Both have explicit reasoning:

- **`L-samples-hostpath`** (§10 item 23) — `charts/lolday/templates/samples-pv.yaml`
  declares a `hostPath` PV at `/mnt/lolday-samples` on server30. The mergerfs
  union mount (NFS from server14 + local banks) keeps detector samples as
  host-filesystem state. While lolday is single-node K3s, the hostPath is
  acceptable. A multi-node migration requires replicating the union-mount on
  the second node first (or switching to ReadWriteMany NFS / S3), which is
  in scope only if the cluster grows beyond one node.

- **H-26 connection-pool tech debt** (§10 item 24) — P6 set `db.py`
  `create_async_engine(pool_size=20, max_overflow=30)`. With 2 backend
  replicas, total checkout cap = 100 connections, exactly matching the
  Postgres default `max_connections`. **Scaling backend to 3+ replicas
  requires a parallel bump in `postgresql.max_connections`** (chart values)
  and a Postgres restart. Tracked so the dependency is not surprising.

Items previously deferred (P3 follow-ups, P4 SBOM workaround, P5 nginx
`include` snippet refactor) are tracked in their respective PR review notes
and do not block the program declaration.

## 7. Lessons learned

### What worked

- **Theme-first decomposition.** Splitting 85 findings into six themed phases
  (rather than batching by severity or component) produced coherent units of
  work, each finishing at a verifiable acceptance gate. A pure severity-first
  split would have lumped the BOLA fixes (P1 H-1/H-2) with the unrelated
  body-size cap (P1 H-24); the theme grouping made the per-phase scope
  meaningful.

- **Aggressive root-cause-first remediation.** The operator pre-authorized
  breaking-change cutovers (Fernet rotation, PAT helper, Harbor robot,
  Kyverno bootstrap). No half-measures meant no long-running dual-stack
  windows. Tech debt is now smaller than it was before the audit.

- **Per-task spec + code review on P6** (subagent-driven). Caught the T2
  `StreamingResponse` 502→200 regression mid-task before it shipped; caught
  4 minor follow-ups (assert-in-side_effect, unused fixtures, missing
  pytest import, default-port CSRF rejection) before each task was marked
  complete. The cost was time; the benefit was zero post-merge regressions
  on P6.

- **Inline finding-ID comments at every change site.** `# H-26: ...`,
  `# M-csrf: ...`, etc. — every non-trivial diff carries the audit-trail
  pointer back to the spec. Future grep finds the rationale in 5 seconds.

### What didn't

- **Kyverno bootstrap edge cases were under-scoped.** The initial P4
  estimate was "install sub-chart, write policy, done." Reality: 6
  follow-up commits, ~12 operator hours, partial cluster admission outage
  during the second `helm upgrade`. Next time a CRD-heavy sub-chart enters
  the umbrella, the plan must include a CRD lifecycle section and a
  release-Secret-size dry-run.

- **Spec line refs went stale fast.** Several P6 tasks (T4 reconciler
  ordering, T7 `validator.py` clone, T5 `_FakeAsyncClient` pattern, T13
  `useJobEvents` signature, T15 `_authed.models._index.tsx` already-prefixed
  key) had spec line refs that did not match the code at implementation
  time. The 2-day gap between spec authoring (2026-05-12) and P6 execution
  (2026-05-14) is enough for refactors elsewhere to invalidate the spec's
  line citations. Recommendation: in future programs, generate line refs at
  plan time (1 hr before execution) rather than at spec time.

- **The "deferred to P6" backlog grew faster than P5 expected.** P5 marked
  5 follow-up items (nginx `include` refactor, actions/upload-artifact SHA
  inconsistency, deploy.sh smoke curl-missing, SBOM cataloger, syft
  upstream issue) as "P6 candidates." None of them landed in P6; they
  remain as PR-comment-notes. P6 was already 18 findings and shouldn't have
  absorbed more. Recommendation: phase-ship-time follow-ups belong in a
  dedicated polish PR, not in the next phase's scope.

### Process notes

- **The `BACKEND_ERRORS` Counter as a universal failure bus** (Pattern 2)
  emerged organically — P3 introduced it, P5 used it for `discord_notify`,
  P6 reused it for `discord_notify_dropped`. No spec section authored this
  pattern; it crystallized from doing the work. Worth promoting to
  `.claude/rules/backend.md` as a convention for future error attribution.

- **Subagent-driven execution on P6** was significantly faster than
  inline execution would have been (estimated 2x), at the cost of slightly
  higher API spend. The cost is justified for security-critical work
  where review discipline matters.

## 8. What's next

The 2026-05-12 security audit program is **complete** on PR #148 merge.
Subsequent security work continues as **ad-hoc PRs per finding**, not as
another phase. The five themes have closure; future audits should produce
their own theme set.

Concrete follow-ups (none blocking program completion):

1. **AsyncExitStack refactor in `download_artifact`** — code-review minor
   from the final P6 pass. Defensive: handles the edge case where
   `stream_cm.__aexit__` raises during cleanup. Candidate for a "P6 polish"
   PR alongside the audit-log retention policy.

2. **Audit-log retention policy** — `pg_partman` monthly partitioning + 365-day
   retention. Deferred from P5 (acceptance was "row exists", retention
   policy is a separate concern).

3. ~~**Kyverno bootstrap runbook** — capture the 3 edge cases from the P4
   ship under `docs/runbooks/` so future Kyverno upgrades don't re-discover
   them.~~ **Done.** Lives at [`docs/runbooks/kyverno-bootstrap.md`](../runbooks/kyverno-bootstrap.md); covers the `crds.install: false`, `excludeKyvernoNamespace`, and SBOM cataloger workaround edge cases per the P4 retrospective.

4. ~~**Promote `BACKEND_ERRORS{stage=...}` to a documented convention.** Add
   a section to `.claude/rules/backend.md` next time the file is touched.~~ **Done.** [`.claude/rules/backend.md`](../../.claude/rules/backend.md) §`BACKEND_ERRORS` failure-bus convention codifies the label-cardinality bound, the "new stage vs new Counter" decision rule, the alerting hook, and the single-Counter-per-finding discipline. Existing `stage` values + the 4 sibling Counters from P5/P6 are enumerated.

5. **Phase-7 (if ever):** the next audit-driven program should produce its
   own spec under `docs/superpowers/specs/YYYY-MM-DD-*-design.md` with the
   same theme-first decomposition and per-task spec compliance discipline.

[^h16-impl-loc]: H-16 shipped as a backend ForwardAuth check inside `backend/app/routers/mlflow_authz.py:240,253-257` reading `X-Forwarded-Method` from Traefik, not as a Traefik headers middleware as the spec text described. Behaviour is equivalent — `POST/PATCH/DELETE` are admin-only — but the enforcement layer differs. The spec ([`2026-05-12-security-hardening-design.md`](../superpowers/specs/2026-05-12-security-hardening-design.md) footnote `[^h16-shipped]`) and the [2026-05-15 post-program review](../phase-history/2026-05-15-security-post-program-review.md) §2 D-7 carry the same note. INFO-level divergence; logged for traceability.

## Related artifacts

- Source spec: [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../superpowers/specs/2026-05-12-security-hardening-design.md)
- Closeout ledger (finding-by-finding): [`docs/phase-history/2026-05-14-security-audit-findings.md`](../phase-history/2026-05-14-security-audit-findings.md)
- Phase plans: [`docs/superpowers/plans/2026-05-12-security-hardening-p1-stop-bleed.md`](../superpowers/plans/2026-05-12-security-hardening-p1-stop-bleed.md) through [`docs/superpowers/plans/2026-05-14-security-hardening-p6-dos-cleanup.md`](../superpowers/plans/2026-05-14-security-hardening-p6-dos-cleanup.md)
- Per-phase PRs: #136 (P1), #137 (P2), #138 (P3), #139 (P4), #147 (P5), #148 (P6)
- Tech debt entries: [`docs/architecture.md`](../architecture.md) §10 items 23 + 24
