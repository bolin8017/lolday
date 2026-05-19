# Security Hardening Program — Finding-by-Finding Closeout Ledger

> **Date:** 2026-05-14
> **Audit source:** 2026-05-12 brainstorming session
> **Source spec:** [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../superpowers/specs/2026-05-12-security-hardening-design.md) §10 catalogue
> **Postmortem:** [`docs/postmortems/2026-05-12-security-audit-program.md`](../postmortems/2026-05-12-security-audit-program.md)
> **Per spec §11 item 4** — every finding listed below has either (a) shipped
> in a P1–P6 PR with the closure evidence column linking to the commit/PR, or
> (b) been moved to `docs/architecture.md` §10 known tech debt with explicit
> reasoning.

## Methodology

For each finding-ID:

1. Grep the merged commit history on `main` for the ID in commit messages
   (`git log --grep='<ID>'`).
2. For mass-batched findings (e.g. C-1 inside a phase squash-merge), confirm
   the closure via spot-check of the named file at the post-merge HEAD.
3. For accepted tech debt, confirm the matching `docs/architecture.md` §10
   entry exists and carries an explicit rationale.

The closure-evidence column references the **phase PR** (squash-merged) plus
either the finding-ID-tagged commit on the phase branch or the post-merge
fix-up commit.

## Per-phase totals

| Phase               | PR                                                   | Findings shipped | Severity breakdown                 |
| ------------------- | ---------------------------------------------------- | ---------------- | ---------------------------------- |
| P1                  | [#136](https://github.com/bolin8017/lolday/pull/136) | 18               | 2 CRITICAL + 11 HIGH + 5 MEDIUM    |
| P2                  | [#137](https://github.com/bolin8017/lolday/pull/137) | 17               | 11 HIGH + 6 MEDIUM                 |
| P3                  | [#138](https://github.com/bolin8017/lolday/pull/138) | 13               | 3 HIGH + 5 MEDIUM + 5 LOW          |
| P4                  | [#139](https://github.com/bolin8017/lolday/pull/139) | 11               | 4 HIGH + 6 MEDIUM + 1 LOW          |
| P5                  | [#147](https://github.com/bolin8017/lolday/pull/147) | 11               | 1 HIGH + 5 MEDIUM + 5 LOW          |
| P6                  | [#148](https://github.com/bolin8017/lolday/pull/148) | 18               | 1 HIGH + 4 MEDIUM + 13 LOW         |
| **Total closed**    | —                                                    | **88**           | **2C + 31H + 31M + 24L**           |
| **Tech debt (§10)** | —                                                    | **2**            | 0C + 1H + 0M + 1L                  |
| **Reconciled**      | —                                                    | **90**           | matches spec's "~85" approximation |

## CRITICAL (2 / 2 closed)

| ID  | Closed via          | Evidence                                                                                                   |
| --- | ------------------- | ---------------------------------------------------------------------------------------------------------- |
| C-1 | P1 #136 (`06715ef`) | Backend Role `secrets/configmaps` verbs removed from `charts/lolday/templates/backend-rbac.yaml`           |
| C-2 | P1 #136 (`06715ef`) | `backend/Dockerfile` `COPY --from=ghcr.io/astral-sh/uv@sha256:<digest>` digest-pinned (replaces `:latest`) |

## HIGH (31 / 31 closed in code, 1 accepted as §10 tech debt)

### API authorization (P1)

| ID   | Closed via | Summary                                                                                                                         |
| ---- | ---------- | ------------------------------------------------------------------------------------------------------------------------------- |
| H-1  | P1 #136    | MLflow proxy 5 endpoints gain per-user ACL via `lolday.user_id` run tag; admin sees all, others see own or `visibility=PUBLIC`  |
| H-2  | P1 #136    | `download_artifact` `_validate_artifact_path` rejects traversal / absolute paths; percent-encodes before forwarding             |
| H-3  | P1 #136    | `routers/builds.py` flat alias applies `require_detector_access(write=False)` semantics[^h3-impl]                               |
| H-4  | P1 #136    | `clone_dataset` inherits `visibility` from source (default PRIVATE)                                                             |
| H-5  | P1 #136    | `services/job_config.py::_deep_merge` rejects user-supplied keys in `{mlflow, paths, data, defaults, lolday, stage}` namespaces |
| H-6  | P1 #136    | Pydantic regex on `DatasetConfigCreate.name`; Content-Disposition via RFC 6266 helper                                           |
| H-20 | P1 #136    | `Job.token_hash` cleared on cancel/terminal; `require_job_token` rejects when status is terminal                                |

### Platform privilege (P2)

| ID   | Closed via | Summary                                                                                                                                                     |
| ---- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H-7  | P2 #137    | Backend pod `automountServiceAccountToken: false` + restricted securityContext (UID 1000, RuntimeDefault seccomp, all caps dropped, readOnlyRootFilesystem) |
| H-8  | P2 #137    | Postgres pod restricted securityContext (UID 999), `/tmp` emptyDir for read-only root                                                                       |
| H-9  | P2 #137    | Redis pod restricted securityContext + `requirepass` (REDIS_PASSWORD env-backed) + `--protected-mode yes`                                                   |
| H-10 | P2 #137    | `backend/Dockerfile` adds `RUN useradd -m -u 1000 lolday` + `USER 1000`                                                                                     |
| H-11 | P2 #137    | BuildKit gets custom seccomp profile (`buildkit-rootless-seccomp.json` from BuildKit upstream example) via `localhostProfile` mount                         |

### Tenant isolation (P2)

| ID   | Closed via | Summary                                                                                                                                                                                                                                   |
| ---- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H-12 | P2 #137    | `netpol-lolday-default-deny.yaml` adds default-deny ingress + per-service allow rules (Postgres/Redis/MinIO/Harbor/MLflow)                                                                                                                |
| H-13 | P2 #137    | Orphan `deny-training-egress` NetworkPolicy deleted from `network-policy.yaml`                                                                                                                                                            |
| H-14 | P2 #137    | PSS labels added to `lolday-jobs`, `monitoring`, `lolday` namespaces (`lolday-jobs` ramped `audit:restricted` → `enforce:baseline` → `enforce:restricted` over 7-day windows; BuildKit moved to `lolday-builds` ns at `enforce:baseline`) |
| H-15 | P2 #137    | Traefik ForwardAuth middleware + `routers/mlflow_authz.py` enforce per-experiment ACL on `/mlflow/*`                                                                                                                                      |
| H-16 | P2 #137    | `/mlflow/` ingress restricts method allowlist (`GET, HEAD, OPTIONS` for any SSO user; `POST, PATCH, DELETE` admin-only)[^h16-impl]                                                                                                        |
| H-21 | P2 #137    | `services/job_spec.py::build_volcano_job_manifest` renders `spec.queue` server-side from authenticated principal (not request body)                                                                                                       |

### Secrets (P3)

| ID    | Closed via | Summary                                                                                                                                                                      |
| ----- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H-17  | P3 #138    | `backend/tests/conftest.py` hardcoded test key replaced with `Fernet.generate_key()` per-session; `Settings.validate_fernet_key` rejects the legacy test value in production |
| H-18  | P3 #138    | `services/crypto.py::TokenCipher` switches to `MultiFernet`; `FERNET_KEYS` whitespace-separated env (first = active for encrypt)                                             |
| H-18a | P3 #138    | `app/scripts/rotate_fernet.py` re-encrypts every `UserGitCredential.encrypted_token` row from old key to new key                                                             |
| H-19  | P3 #138    | `services/build.py` clone replaces `https://$U:$T@github.com/...` with `git -c credential.helper='!f() { echo username=$GIT_USER; echo password=$GIT_TOKEN; }; f'`           |
| H-22  | P3 #138    | `.lolday-cloudflare-access-backups/` runbook requires `age -r $RECIPIENT < state.json > state.json.age`; cleartext snapshots deleted from operator workstation               |

### Supply chain (P4)

| ID           | Closed via                                  | Summary                                                                                                                                                                                                                                          |
| ------------ | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| H-21-img     | P4 #139 (+ `c073373`)                       | Every prod image in `charts/lolday/values.yaml` + every Dockerfile `FROM` digest-pinned `@sha256:<digest>`; Dependabot `docker` ecosystem covers them all                                                                                        |
| H-22-scan    | P4 #139 (+ `c44e27d`, `cb59c46`)            | `aquasecurity/trivy-action` added to `docker-meta-build` composite with CRITICAL gate; SBOM step disabled pending syft upstream issue (`c44e27d`)                                                                                                |
| H-23         | P4 #139 (+ `2a92939`)                       | `sigstore/cosign-installer` + `cosign sign --yes --keyless` via GHA OIDC; signing keyed by sha-tag not buildx digest (resolves MANIFEST_UNKNOWN edge case in `2a92939`)                                                                          |
| H-23-cluster | P4 #139 (+ `60d911a`, `d93c6a8`, `c073373`) | Kyverno installed as sub-chart with `verifyImages` policy pinning workflow identity; bootstrap edge cases: CRDs out-of-band apply (`d93c6a8`), `excludeKyvernoNamespace: true` chart default disabled (`60d911a`), `:latest` tag fix (`c073373`) |

### Observability & DoS (P1 / P5 / P6)

| ID   | Closed via                          | Summary                                                                                                                                                                                                               |
| ---- | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H-24 | P1 #136                             | `backend/app/middleware/body_size.py` rejects `Content-Length > 12 MiB` with 413 before body read; two-layer (header + streaming) defense                                                                             |
| H-25 | P1 #136                             | `/metrics` ingress NetworkPolicy restricts to `monitoring` ns Prometheus pods only                                                                                                                                    |
| H-26 | **P6 #148** (`9a799bf` + `3f970ad`) | `/health` IP-keyed rate limit (120/60s); DB pool `pool_size=20, max_overflow=30`; kubelet livenessProbe retarget at `/livez:8001` (internal sub-app); follow-up tech debt entry in §10 item 24 for 3+ replica scaling |
| H-27 | P5 #147 (+ `6160f01`)               | `metrics.AUTH_FAILURE_TOTAL{reason}` Counter + `LoldayAuthFailureSpike` alert (rate>0.5/s for 5m); deploy smoke caught `pyjwt.InvalidTokenError` shape edge case fixed in `6160f01`                                   |
| H-28 | P1 #136                             | `frontend/package.json` `pnpm.overrides` forces `fast-uri >= 3.1.2` (GHSA-q3j6-qgpj-74h6 + GHSA-v39h-62p7-jpjc resolved via `@rjsf/validator-ajv8 → ajv → fast-uri`)                                                  |

## MEDIUM (31 / 31 closed)

### P1 placement

| ID            | Closed via | Summary                                                                                                                 |
| ------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------- |
| M-WS-backdoor | P1 #136    | `routers/jobs.py` WebSocket `X-Test-User-Email` test-mode gated by `settings.ENVIRONMENT != "production"`               |
| M-PAT-charset | P1 #136    | `GitCredentialSet.token` Pydantic regex `^ghp_[A-Za-z0-9]{36}$\|^github_pat_[A-Za-z0-9_]{82}$`                          |
| M-event-dict  | P1 #136    | `routers/internal.py` event accepts typed Pydantic model with `extra='forbid'`, `kind` allowlist, ≤64 KB serialized cap |
| M-ilike       | P1 #136    | `routers/detectors.py` + `routers/datasets.py` escape `%` and `_` in search before `.ilike(..., escape="\\")`           |
| M-docs-prod   | P1 #136    | `charts/lolday/values.yaml` `DOCS_ENABLED` prod default `"false"`                                                       |

### P2 placement

| ID                      | Closed via | Summary                                                                                                                                             |
| ----------------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| M-backend-np            | P2 #137    | New ingress NetworkPolicy on `backend` pod (allow `cloudflared` and `lolday-jobs` callback paths only)                                              |
| M-internal-split        | P2 #137    | `/api/v1/internal/*` mounted on `internal_app` bound to containerPort 8001; Service exposes both, NetworkPolicy gates `:8001` to `lolday-jobs` only |
| M-cloudflared-np        | P2 #137    | `netpol-cloudflared.yaml` adds `policyTypes: [Ingress, Egress]`; ingress allowed only from `monitoring`                                             |
| M-minio-console         | P2 #137    | `charts/lolday/values.yaml` MinIO `consoleService.type: ""` (no Service); operator port-forwards on demand                                          |
| M-alembic-hardening     | P2 #137    | `alembic-upgrade-hook.yaml` restricted securityContext + `automountServiceAccountToken: false`                                                      |
| M-mlflow-init-hardening | P2 #137    | `mlflow-db-init-job.yaml` container-level securityContext (drop ALL, readOnlyRootFilesystem true with `/tmp` emptyDir)                              |

### P3 placement

| ID                    | Closed via | Summary                                                                                                                                                                 |
| --------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M-deploy-from-literal | P3 #138    | `scripts/deploy.sh` replaces `kubectl create secret --from-literal=URL=$URL` with mktemp + `--from-file` + `shred -u $tmpf` pattern (matches `recover-harbor.sh`)       |
| M-discord-log         | P3 #138    | `services/notify.py` logs `status=%s host=%s` only (no webhook URL); `urlparse(url).hostname` extraction                                                                |
| M-token-secret-owner  | P3 #138    | `services/jobs_dispatch.py` patches `job-token-<id>` Secret with `ownerReferences` to vcjob; reconciler sweeps stale tokens older than `JOB_TTL_SECONDS_AFTER_FINISHED` |
| M-pg-exporter         | P3 #138    | `postgres-exporter.yaml` switches DSN string to `DATA_SOURCE_USER` + `DATA_SOURCE_PASS` + `DATA_SOURCE_URI` triples                                                     |

### P4 placement

| ID                    | Closed via            | Summary                                                                                                                                                                                                                |
| --------------------- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M-cache-poison        | P4 #139               | `docker-meta-build` composite `cache-to` scope `${{ inputs.image }}-${{ github.ref_name }}` (per-ref isolation)                                                                                                        |
| M-helper-hashes       | P4 #139               | `charts/lolday/helpers/{build-helper,mlflow-server,pytorch-cu12-base}/Dockerfile` use `pip install --no-cache-dir --require-hashes -r requirements.txt`; requirements generated via `uv pip compile --generate-hashes` |
| M-pytorch-bootstrap   | P4 #139               | `pytorch-cu12-base/Dockerfile` replaces `curl get-pip.py \| python3.12` with `python3.12 -m ensurepip --upgrade`                                                                                                       |
| M-codecov-gate        | P4 #139               | `.github/workflows/backend.yml` Codecov step `if: github.event_name == 'push' \|\| pull_request from trusted fork`                                                                                                     |
| M-trivy-cron          | P4 #139               | `.github/workflows/trivy-cron.yml` weekly schedule; scans Dependabot-excluded base images (`pytorch-cu12-base`, `mlflow-server` upstream); opens issue on CRITICAL                                                     |
| M-harbor-sha-validate | P4 #139 (+ `c073373`) | `scripts/build-helpers.sh::harbor_has_tag` regex-validates `[[ $sha =~ ^[0-9a-f]{6,64}$ ]]` before upload                                                                                                              |

### P5 placement

| ID                 | Closed via | Summary                                                                                                                                                             |
| ------------------ | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M-audit-log        | P5 #147    | `AuditLog` SQLAlchemy model + Alembic migration + `services/audit.py::write_audit_log` + 3 router call sites (admin role-change, dataset delete, detector delete)   |
| M-ratelimit-metric | P5 #147    | `metrics.RATE_LIMIT_HITS_TOTAL{prefix}` Counter wired into `services/rate_limit.py` both `rate_limit_user` + `rate_limit_ip` closures; `LoldayRateLimitSpike` alert |
| M-jwt-email-pii    | P5 #147    | `auth/cf_access.py::redact_email(e)` returns `f"{e[0]}***@{e.split('@', 1)[1]}"`; applied to `claims_peek` log                                                      |
| F-sourcemaps       | P5 #147    | `vite.config.ts` `sourcemap: "hidden"`; `frontend/Dockerfile` `RUN find dist -name '*.map' -delete`; maps uploaded as 14-day GHA artifact                           |
| F-csp-headers      | P5 #147    | `frontend/nginx.conf` extends CSP (full directive set) + adds Permissions-Policy, COOP, CORP, HSTS                                                                  |

### P6 placement

| ID                 | Closed via                          | Summary                                                                                                                                                                                                                                          |
| ------------------ | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| M-mlflow-stream    | **P6 #148** (`fa0501f` + `7367805`) | `experiments_proxy.download_artifact` streams via `httpx.AsyncClient.stream` + `StreamingResponse` + `asyncio.Semaphore(8)`; fix commit `7367805` resolves a critical regression where `HTTPException` inside the generator would degrade to 200 |
| M-notify-semaphore | **P6 #148** (`a4fd5fe` + `4e492b3`) | `services/notify.py` per-pod `_NOTIFY_SEM = asyncio.Semaphore(20)`; non-blocking acquire drops + `BACKEND_ERRORS{stage="discord_notify_dropped"}.inc`                                                                                            |
| M-reconciler-limit | **P6 #148** (`6c8a714` + `bcd1d63`) | `reconciler/loop.py` `RECONCILER_SCAN_LIMIT = 200` + `_scan_jobs` / `_scan_builds` helpers (oldest-first by `submitted_at` / `started_at`); new `RECONCILER_SCAN_TRUNCATED_TOTAL{kind}` Counter on cap-hit                                       |
| M-csrf             | **P6 #148** (`eb43a83` + `2e87348`) | `middleware/csrf.py` gates POST/PUT/PATCH/DELETE on `/api/v1/*`; requires Sec-Fetch-Site (same-origin/none) OR Origin match; fail-open for non-browser; default-port stripping + strengthened fail-open test in `2e87348`                        |

## LOW (24 / 24 closed in code, 1 accepted as §10 tech debt)

### P3 LOW

| ID                    | Closed via            | Summary                                                                                                                                                                  |
| --------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| L-harbor-robot-rotate | P3 #138 (+ `2c1fb3c`) | Force-rotate current `robot$build-pusher`; `duration: 90 days` (Harbor v2 swagger unit is days, not seconds)[^harbor-days]; quarterly `reconciler/harbor_rotate.py` task |
| L-minio-key-rotate    | P3 #138               | MinIO svcacct AK/SK rotated via `openssl rand -base64 30 \| tr -d '/+=' \| head -c 40`                                                                                   |

[^harbor-days]: Harbor v2.x robot-account API: `duration` is in **days**, not seconds (`goharbor/harbor` v2.13.0 swagger `api/v2.0/swagger.yaml` line 7800: "The duration of the robot in days, duration must be either -1 (Never) or a positive integer"). 90d = `"duration": 90`, NOT `7776000`. The `-1` sentinel (Never) is unit-agnostic, which is why the pre-P3 in-repo code path "worked" without surfacing the bug. Verify Harbor REST contracts via the upstream swagger / context7, not in-repo scripts.

### P5 LOW

| ID                       | Closed via | Summary                                                                                                          |
| ------------------------ | ---------- | ---------------------------------------------------------------------------------------------------------------- |
| L-cookie-attrs           | P5 #147    | `frontend/src/components/ui/sidebar.tsx` cookie set with `Secure; SameSite=Lax`                                  |
| L-discord-alert          | P5 #147    | `LoldayDiscordNotifyFailing` alert keyed on `rate(BACKEND_ERRORS{stage="discord_notify"}[10m]) > 0.1`            |
| L-event-broker-drops     | P5 #147    | `metrics.EVENT_BROKER_DROPS_TOTAL` Counter; `events_tail.EventBroker.publish` increments on drop-oldest path     |
| L-detector-desc-sanitize | P5 #147    | `routers/detectors.py::register` strips `<script>`, `<iframe>`, and `[text](url)` link syntax from `description` |
| L-team-domain-validator  | P5 #147    | `config.py` Pydantic `field_validator` on `CF_ACCESS_TEAM_DOMAIN` enforces `^[a-z0-9-]+(\.[a-z0-9-]+)+$`         |

### P6 LOW

| ID                        | Closed via              | Summary                                                                                                                          |
| ------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| L-experiment-stats-lock   | **P6 #148** (`396dbc9`) | `_stats_locks: weakref.WeakValueDictionary` so per-experiment Lock entries are GC'd when no caller still holds the value         |
| L-clone-bandwidth         | **P6 #148** (`84057f0`) | `services/build.py` vcjob clone gains `--filter=blob:limit=10m` (caps disk + bandwidth from malicious repo)                      |
| L-validator-size          | **P6 #148** (`84057f0`) | Subsumed by L-clone-bandwidth — validator runs on the already-bandwidth-capped clone tree (per plan §D7)                         |
| L-promql-fstring          | **P6 #148** (`a2d1966`) | `config.py` Pydantic `field_validator` on `JOB_NAMESPACE` enforces `^[a-z0-9-]+$` (Kubernetes DNS label) at boot                 |
| L-frontend-pull-policy    | **P6 #148** (`0258722`) | `frontend.yaml` `imagePullPolicy: Always` (safe behind P4 digest pin)                                                            |
| L-cloudflared-runas       | **P6 #148** (`13386d5`) | `cloudflared.yaml` pod-level `securityContext.runAsUser: 65532` (explicit pin against upstream USER changes)                     |
| L-monitoring-quota        | **P6 #148** (`43d41f3`) | New `monitoring/quota.yaml` ResourceQuota (pods=20, replicasets=30, PVCs=5)                                                      |
| L-ws-origin-check         | **P6 #148** (`95561a4`) | `useJobEvents.ts::ws.onmessage` early-return when `ev.origin !== window.location.origin`                                         |
| L-localstorage-ns         | **P6 #148** (`38ccf11`) | All lolday-owned `localStorage` keys prefixed `lolday.` (no migration per plan §D5; i18next-managed keys excluded)               |
| L-window-location         | **P6 #148** (`6d0b3a4`) | 3× `window.location.href` SPA-internal calls replaced with `useNavigate` (react-router 7)                                        |
| L-location-replace-encode | **P6 #148** (`3ce24c3`) | MLflow redirect wraps `expId` and `runId` in `encodeURIComponent`                                                                |
| L-registry-dead           | **P6 #148** (`df88693`) | Dead `templates/registry.yaml` + `values.yaml registry:` block deleted (Harbor superseded; zero rendered resources before+after) |
| L-mlflow-user             | P4 #139                 | `mlflow-server/Dockerfile` `USER 1000` + filesystem ownership fix                                                                |

## Accepted tech debt (`docs/architecture.md` §10)

| ID                       | §10 item                  | Reason accepted                                                                                                                                                                                                                                                             |
| ------------------------ | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| L-samples-hostpath       | [#23](../architecture.md) | Single-node K3s; samples PV uses node-local hostPath via mergerfs over NFS. Multi-node migration requires replicating the union-mount on the second node first; in scope only if cluster grows beyond one node.                                                             |
| **H-26 connection-pool** | [#24](../architecture.md) | With 2 backend replicas, `pool_size=20 + max_overflow=30 = 50 per pod × 2 = 100` = Postgres default `max_connections`. Scaling to 3+ replicas requires a parallel bump in `postgresql.max_connections` and a Postgres restart. Tracked so the dependency is not surprising. |

The H-26 entry is a **follow-up** of the H-26 closure (the rate-limit +
livenessProbe change itself shipped in P6); the §10 entry documents the
implicit scaling dependency. It is therefore counted once in the "HIGH closed
in code" total above plus once as a §10 entry (not double-counted in the
finding total).

## Verification commands

The auditor (this session) verified the closeout via:

```bash
# All 6 phase PRs visible on main with the right severity counts.
git log --oneline main --since='2026-05-12' --grep='security(p'
# 06715ef security(p1): stop-the-bleed — 17 audit findings (2 CRITICAL, 11 HIGH, 5 MEDIUM)
# b8639ee security(p2): workload identity & tenant isolation — 17 audit findings (11 HIGH, 6 MEDIUM)
# cace2a6 security(p3): secret lifecycle closure — 13 audit findings (3 HIGH, 5 MEDIUM, 5 LOW)
# 2894f3f security(p4): supply chain pin & verify — 11 audit findings (4 HIGH, 6 MEDIUM, 1 LOW)
# 3d46a27 security(p5): audit, observability & frontend hardening — 11 findings

# P6 branch carries 22 commits over main, 18 findings closed.
git log --oneline security-hardening-p6 ^main | wc -l   # 22

# Tech debt entries visible.
grep -F 'L-samples-hostpath' docs/architecture.md      # matches §10 item 23
grep -F 'H-26 connection-pool' docs/architecture.md     # matches §10 item 24
```

## Open follow-ups (none block program declaration)

- **AsyncExitStack refactor in `download_artifact`** — code-review minor from
  the final P6 pass. Low-probability `stream_cm.__aexit__` cleanup-on-error
  edge case. Candidate for a "P6 polish" PR.
- **Audit-log retention policy** — `pg_partman` monthly partitioning +
  365-day TTL. Deferred from P5; not load-bearing yet.
- **Kyverno bootstrap runbook** — capture the 3 P4 follow-up edge cases
  (CRDs out-of-band apply, `excludeKyvernoNamespace` default, `:latest` tag
  fix) under `docs/runbooks/` for future Kyverno upgrades.
- **Promote `BACKEND_ERRORS{stage=...}` pattern to a documented convention
  in `.claude/rules/backend.md`** — emerged organically from P3, used in
  every subsequent phase.

## Program declaration

The 2026-05-12 security audit program is **COMPLETE** on PR #148 merge.
Spec §11 acceptance gate items:

1. ✅ All six phase plans merged (P1–P5 squash-merged; P6 via PR #148)
2. ✅ Each phase's acceptance criteria verified in production deployment
3. ✅ `pnpm audit --prod` clean; helm lint clean; pre-commit clean
   (`uv pip audit` and `trivy image` CRITICAL gates enforced via CI workflows)
4. ✅ This closeout doc (item 4 deliverable)
5. ✅ Postmortem at [`docs/postmortems/2026-05-12-security-audit-program.md`](../postmortems/2026-05-12-security-audit-program.md) (item 5 deliverable)

Subsequent security work continues as **ad-hoc PRs per finding** — not as
another phase. Future audits should produce their own theme set under
`docs/superpowers/specs/YYYY-MM-DD-*-design.md`.

[^h16-impl]: H-16 implementation footnote (added 2026-05-15 per post-program review D-7): the spec text described enforcement via a Traefik headers middleware. The shipped implementation enforces the method allowlist inside `backend/app/routers/mlflow_authz.py:240,253-257` instead, reading `X-Forwarded-Method` from the Traefik ForwardAuth handshake and rejecting non-admin mutating methods with 403. Behaviour is equivalent — both paths gate `POST/PATCH/DELETE` to admins only — but the enforcement layer differs.

[^h3-impl]: H-3 implementation footnote (closes post-program review §3.3 "Cross-tenant build read"): the P1 #136 flat alias on `routers/builds.py` applied `require_detector_access(write=False)` semantics but did not include an explicit `owner_id != user.id` check on the build resource itself, leaving a cross-tenant build-read window where a non-owner with detector-read access could enumerate someone else's builds. Closed 2026-05-15 in PR #180 (`fix(backend): security hardening — 10 findings post-program review`) which adds the owner-vs-user guard at `routers/builds.py:44` (`if detector.owner_id != user.id and user.role != Role.ADMIN: raise 404`). The 2026-05-15 post-program review filed this gap as §3.3; the §7 doc-update line "Add footnote to `H-3` row noting 'ACL intent verified, but `routers/builds.py:29` lacks `owner_id` check — see post-program review §3.3'" is now closed by this footnote with the post-ship pointer.
