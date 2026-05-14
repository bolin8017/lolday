# Security Hardening — Design Specification

> **STATUS: SHIPPED 2026-05-14.** All six phase plans merged
> (P1 #136, P2 #137, P3 #138, P4 #139, P5 #147, P6 #148). 88 finding-IDs
> closed in code + 2 accepted as `docs/architecture.md` §10 tech debt.
>
> - Finding-by-finding closeout ledger: [`docs/phase-history/2026-05-14-security-audit-findings.md`](../../phase-history/2026-05-14-security-audit-findings.md)
> - Program postmortem (5 root-cause themes + lessons learned): [`docs/postmortems/2026-05-12-security-audit-program.md`](../../postmortems/2026-05-12-security-audit-program.md)
> - Kyverno bootstrap runbook (P4 follow-up edge cases): [`docs/runbooks/kyverno-bootstrap.md`](../../runbooks/kyverno-bootstrap.md)
> - `BACKEND_ERRORS` failure-bus convention: [`.claude/rules/backend.md`](../../../.claude/rules/backend.md)
>
> Subsequent security work is **ad-hoc PRs per finding**, not another phase.
> The sections below are preserved as the authoritative spec ledger.

> **Created 2026-05-12.** Trigger: a comprehensive seven-domain security audit
> of the Lolday platform identified **2 CRITICAL, ~30 HIGH, ~30 MEDIUM, ~25
> LOW** findings spanning AuthN/AuthZ, injection/RCE/SSRF, secrets/crypto,
> CI/CD supply chain, K8s/Helm posture, frontend, DoS/observability, and
> dependency CVEs. Source data: the audit reports captured in the
> brainstorming session of 2026-05-12 (no separate audit-findings doc — this
> spec is the authoritative ledger).

> **This spec is the umbrella design.** The 85 findings are decomposed into
> six implementation phases (P1–P6), each with its own plan file under
> `docs/superpowers/plans/`. The spec is the **what + why + acceptance
> criteria**; the plans are the **how + step-by-step**.

## 1. Overview

The audit confirmed the platform has many _correct_ basics — Cloudflare
Access SSO is the single auth path, all SQL goes through SQLAlchemy ORM,
there is no `pickle`/`yaml.load`/`eval`, JWT verification pins `RS256` and
checks `aud/iss/exp/iat`, no XSS sinks in the frontend, GitHub Actions
references are SHA-pinned per `.claude/rules/github-actions.md`.

The risk surface that remains clusters around **defence-in-depth gaps**:
when a single component is compromised, the blast radius is the entire
cluster. The audit identified five root-cause themes that recur across
domains:

| Theme                                               | Why it matters                                                                                                                                                                                                                                                               |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Backend pod is a god-node**                    | The backend ServiceAccount in `lolday` ns has `secrets: get/list/create/delete`. The `lolday` ns holds MinIO root, Postgres, Fernet, Harbor admin, MLflow DB, Cloudflared tunnel, Discord webhook secrets. Backend RCE → cluster-wide credential theft in one hop.           |
| **B. Jobs ns multi-tenancy is paper-thin**          | `lolday-jobs` has no Pod Security Standards label, BuildKit runs with `seccompProfile: Unconfined`, MLflow has no authn for cluster-internal traffic, and `/internal/*` cross-ns trust depends entirely on every route correctly attaching `require_job_token`.              |
| **C. MLflow is the platform's biggest BOLA window** | The proxy authenticates with `current_active_user` but never filters by owner. Five endpoints (`list_experiments`/`list_runs`/`get_run`/`list_artifacts`/`download_artifact`) leak across users; the `path` parameter has no `../` block.                                    |
| **D. Secret lifecycle has no closure**              | A well-known Fernet test key is committed to the repo; single Fernet key with no `MultiFernet` rotation path; Git PATs are URL-embedded in `git clone`; Harbor robot account never expires (`duration: -1`); Discord webhook URLs leak into logs via `httpx` exception repr. |
| **E. Image supply chain stops at the tag**          | `backend/Dockerfile` uses `COPY --from=ghcr.io/astral-sh/uv:latest`. No prod image is digest-pinned. No Trivy in CI. No Cosign signing. BuildKit GHA cache scope is shared between PR and main.                                                                              |

Treating these themes as the unit of work (rather than the 85 individual
findings) is the central design decision of this spec: each phase below
attacks one or two themes, lets us finish a coherent unit, and produces
verifiable acceptance criteria at the phase boundary.

## 2. Authorization

The operator authorized the following at the 2026-05-12 brainstorming
gate:

- **Aggressive root-cause-first remediation.** No half-measures, no
  long-running dual-stack windows, no "defer to a later phase" if a
  finding has a clean fix today. Per `~/.claude/CLAUDE.md` §Root-cause
  first, structural fixes win over surface patches.
- **Force-rotate three breaking secrets** in a single cutover:
  - **Fernet master key.** All `UserGitCredential.encrypted_token` rows
    re-encrypted under the new key during a one-shot maintenance window
    (script-driven). No dual-key tolerance window beyond the
    minutes-long rotate procedure. Implemented via `MultiFernet`
    primitive to make the rotate itself atomic, then the old key is
    retired.
  - **Git PAT URL pattern.** The legacy inline `https://$U:$T@...`
    pattern is removed in one PR; build jobs that haven't picked up the
    new image MUST be drained or cancelled before merge.
  - **Harbor robot account.** The current `robot$build-pusher` is
    rotated and the duration is set to 90 days; subsequent rotations
    are scheduled by a reconciler task.
- **Six-phase decomposition** of the 85 findings — by root-cause theme,
  not by severity or by component.
- **Decisive open-question answers** baked into this spec (see §8). No
  spec-level questions are deferred to plan or implementation time.

## 3. Threat Model & Assumptions

The platform's threat model determines which findings are real risks
versus theoretical hygiene. The audit was conducted under these
assumptions:

| Layer                    | Trusted                                                                                                                                 | Not trusted                                                                                                                                            |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **External traffic**     | Cloudflare Access SSO (JWT signed by CF's JWKS, allowlisted IdPs + email allowlist)                                                     | Anyone whose JWT we can't verify — including a malicious user who has obtained a service-token CN                                                      |
| **Operator**             | The operator on `server30` (root via `sudo` when granted; no IPMI / out-of-band)                                                        | Compromised operator workstation — `.lolday-secrets.env` and `.lolday-cloudflare-access-backups/` are operator-local, leaked on workstation compromise |
| **In-cluster pods**      | `backend`, `frontend`, `mlflow`, `cloudflared`, the chart-managed infra pods                                                            | **Job pods** — they run third-party (detector author) code. Treat as adversarial.                                                                      |
| **Detector authors**     | Their decision-making about model hyperparameters / thresholds (per `docs/architecture.md` §1.2 deploy-platform stance)                 | Their published code at runtime — a compromised maldet release, a malicious dependency, a typo-squatted PyPI package                                   |
| **Third-party services** | Cloudflare Access (issuer of the JWTs we verify), GitHub (source for detector repos), Discord (webhook delivery only — fire-and-forget) | The contents of any detector's git repo (treated as untrusted input)                                                                                   |

Out of scope for this spec (handled by other layers):

- Server30 OS hardening (kernel sysctls, `auditd`, UFW, sshd_config) —
  separate runbook ownership.
- Physical security of the host (covered by the lab's facility security).
- IdP-side hardening (CF Access policy authoring is the operator's call,
  not a code-level concern).

## 4. Scope

### 4.1 In scope

All 85 findings catalogued in §10 below, organized into six phases:

1. **P1 — Stop the bleed** (1 week). CRITICAL findings + the subset of
   HIGH that are reachable by a single authenticated request.
2. **P2 — Workload identity & tenant isolation** (2 weeks). Theme A + B
   in full. RBAC narrowing, pod securityContext, NetworkPolicy
   default-deny, PSS labels, BuildKit seccomp, MLflow internal authn.
3. **P3 — Secret lifecycle closure** (1 week). Theme D in full.
   `MultiFernet` rotation, Git PAT credential helper, Harbor robot
   rotation, log redaction, encrypted backups.
4. **P4 — Supply chain pin & verify** (2 weeks). Theme E in full.
   Digest-pinning every image, Trivy in CI, Cosign signing + Kyverno
   verification, hash-pinned helper pip installs.
5. **P5 — Audit, observability, frontend hardening** (1 week). Audit
   log table, auth-failure metric, rate-limit metric, JWT email
   redaction, CSP / Permissions-Policy / COOP / CORP / HSTS, source-map
   stripping.
6. **P6 — DoS, residual MEDIUM, LOW cleanup** (1–2 weeks). Body-size
   cap, /health rate limit, streaming artifact proxy, CSRF middleware,
   and the long tail of LOW findings.

### 4.2 Out of scope

- The original audit report as a standalone file. This spec is the
  authoritative ledger; the report exists only in the conversation
  history of 2026-05-12.
- Live penetration testing. The audit was code-review only; some
  findings depend on deployment configuration (Cloudflare tunnel
  routing rules, NetworkPolicy CNI enforcement) that requires cluster
  inspection to confirm.
- A net-new compliance framework (SOC2, ISO 27001). The audit anchors
  on OWASP Top 10 (2023), OWASP ASVS L2, CIS Kubernetes Benchmark,
  NSA-CISA Kubernetes Hardening Guide, NIST SP 800-\* where directly
  relevant.
- Detector-side hardening (the `maldet`, `elfrfdet`, `elfcnndet` repos
  individually). Lolday's job is to contain a compromised detector
  pod, not to make the detector itself unbreakable.

## 5. Architecture

The six phases are designed to be **as decoupled as possible**:

```
P1 (stop-the-bleed)           ─┐
                               ├─ Independent. Different files.
P2 (workload identity)        ─┘   P2 depends on P1 only at the
                                    Kyverno-install step (deferred
                                    to P4 in practice).

P3 (secret lifecycle)         ──── Independent file set. No
                                    runtime dependency on P1/P2.

P4 (supply chain)             ──── Touches CI workflows + chart
                                    image references. The
                                    Kyverno admission policy
                                    introduced here also enforces
                                    PSS labels from P2.

P5 (audit + obs + frontend)   ──── Touches backend metric module,
                                    new audit_log model, frontend
                                    nginx.conf. No code-path
                                    collision with P1-P4.

P6 (DoS + residual)           ──── Final clean-up. By design picks
                                    up anything that was deferred.
```

The shared cross-cutting components introduced in this program:

- **`AuditLog` SQLAlchemy model** + Alembic migration (P5).
- **Kyverno** (single point of admission control for both PSS
  enforcement and image-signing verification; chosen over OPA
  Gatekeeper per §8).
- **Traefik ForwardAuth middleware** for MLflow internal traffic
  (decodes CF Access JWT or per-job token, enforces per-experiment
  ACL).
- **`scripts/rotate_fernet.py`** — one-shot data migration tool used
  during P3 rollout and reusable for future key rotation.

## 6. Phase plans

Each subsection is a stub that the corresponding plan file expands. The
**Findings** column references the ID system used in §10. The **Files
touched** column is the union; the plan file enumerates the exact diff
per file.

### 6.1 P1 — Stop the bleed (1 week)

**Goal:** Eliminate any vulnerability reachable by a single authenticated
HTTP request, and patch the one CVE confirmed via `pnpm audit`.

| Finding       | Severity | Summary                                                                                                                                                                                                           |
| ------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| C-1           | CRITICAL | Remove `secrets`/`configmaps` verbs from backend's `lolday` Role (`charts/lolday/templates/backend-rbac.yaml:22-27`).                                                                                             |
| C-2           | CRITICAL | `backend/Dockerfile:7` `COPY --from=ghcr.io/astral-sh/uv:latest` → pin to digest.                                                                                                                                 |
| H-1           | HIGH     | `routers/experiments_proxy.py` `list_experiments`/`list_runs`/`get_run`/`list_artifacts`/`download_artifact`: add owner ACL via `lolday.user_id` tag join, admin sees all, others see own or `visibility=PUBLIC`. |
| H-2           | HIGH     | Same file `download_artifact`: reject `path` containing `..`, `/`, leading `.`; percent-encode before forwarding.                                                                                                 |
| H-3           | HIGH     | `routers/builds.py:28-43` flat alias — apply `require_detector_access(write=False)` semantics.                                                                                                                    |
| H-4           | HIGH     | `routers/datasets.py:223-228` `clone_dataset` — inherit `visibility` from source (default PRIVATE if PRIVATE source).                                                                                             |
| H-5           | HIGH     | `services/job_config.py:117-143` `_deep_merge` — reject user-supplied top-level keys in `{mlflow, paths, data, defaults, lolday, stage}`.                                                                         |
| H-6           | HIGH     | `routers/datasets.py:160-165` — Pydantic regex on `DatasetConfigCreate.name` (`^[A-Za-z0-9 _.\-]+$`); Content-Disposition via RFC 6266 helper.                                                                    |
| H-20          | HIGH     | `routers/jobs.py:644-650`, `reconciler/jobs.py::_finalize_*` — clear `Job.token_hash` on cancel/terminal. `deps.py:67-84` `require_job_token` — reject if `job.status` is terminal.                               |
| H-24          | HIGH     | `backend/app/main.py` — body-size middleware rejecting `Content-Length > 12 MiB` with 413 before body read.                                                                                                       |
| H-25          | HIGH     | `/metrics` — new NetworkPolicy in `lolday` ns restricting ingress to `monitoring` ns Prometheus pods.                                                                                                             |
| H-28          | HIGH     | `frontend/package.json` — `pnpm.overrides` forcing `fast-uri >= 3.1.2` (resolves GHSA-q3j6-qgpj-74h6 and GHSA-v39h-62p7-jpjc via `@rjsf/validator-ajv8 → ajv → fast-uri`).                                        |
| M-WS-backdoor | MEDIUM   | `routers/jobs.py:796-803` WebSocket `X-Test-User-Email` path gated additionally by `settings.ENVIRONMENT != "production"`.                                                                                        |
| M-PAT-charset | MEDIUM   | `GitCredentialSet.token` Pydantic regex `^ghp\_[A-Za-z0-9]{36}$                                                                                                                                                   | ^github*pat*[A-Za-z0-9_]{82}$`. |
| M-event-dict  | MEDIUM   | `routers/internal.py:52-70` — `event: dict[str, Any]` → typed Pydantic model with `extra='forbid'`, `kind` allowlist, ≤ 64 KB serialized cap.                                                                     |
| M-ilike       | MEDIUM   | `routers/detectors.py:268-270` + `routers/datasets.py:122` — escape `%` and `_` in `search` before `.ilike(..., escape="\\")`.                                                                                    |
| M-docs-prod   | MEDIUM   | `charts/lolday/values.yaml:52` — `DOCS_ENABLED` prod default to `"false"`.                                                                                                                                        |

**Files touched (union):** `backend/Dockerfile`, `backend/app/main.py`,
`backend/app/auth/cf_access.py` (test-mode gate),
`backend/app/config.py` (`DOCS_ENABLED` default),
`backend/app/deps.py`, `backend/app/routers/{builds,datasets,detectors,
experiments_proxy,internal,jobs,users_me,credentials,models_registry,
admin,cluster}.py`, `backend/app/schemas/credential.py`,
`backend/app/services/job_config.py`,
`backend/app/services/job_tokens.py`, `backend/app/reconciler/jobs.py`,
`backend/app/middleware/body_size.py` (new),
`charts/lolday/templates/backend-rbac.yaml`,
`charts/lolday/templates/network-policy.yaml` (add `metrics-ingress`),
`charts/lolday/values.yaml`,
`frontend/package.json`,
`frontend/pnpm-lock.yaml`,
plus tests.

**Acceptance criteria:**

1. `kubectl auth can-i get secrets -n lolday --as=system:serviceaccount:lolday:lolday-backend` → `no`.
2. New backend e2e: user A POSTs `/jobs` → completes → user B `GET /api/v1/runs/<run-id>` → 403 / 404. Same for `/artifacts/list` and `/artifacts/download`.
3. New backend e2e: `POST /api/v1/jobs` body `{"params": {"mlflow": {"tracking_uri": "http://evil"}}}` → 400 (reserved namespace).
4. New backend e2e: `POST /api/v1/datasets` body `{"name": "evil\r\nContent-Type: text/html"}` → 400.
5. New backend e2e: complete a job → cancel/finalize → reuse the same `Authorization: Bearer <old_token>` against `/api/v1/internal/jobs/<id>/config` → 401/403.
6. `frontend/pnpm-lock.yaml` `fast-uri` resolves ≥ 3.1.2.
7. `curl --max-time 5 --data-binary @<13MB-file> https://lolday/.../jobs` → 413.

### 6.2 P2 — Workload identity & tenant isolation (2 weeks)

**Goal:** Make a compromised backend or detector pod a _local_ incident,
not a cluster-wide one.

| Finding                 | Severity | Summary                                                                                                                                                                                                                                                                                                                                                |
| ----------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| H-7                     | HIGH     | `charts/lolday/templates/backend.yaml` — `automountServiceAccountToken: false`; pod & container restricted securityContext (`runAsNonRoot: true, runAsUser: 1000, fsGroup: 1000, seccompProfile: RuntimeDefault, capabilities.drop: [ALL], allowPrivilegeEscalation: false, readOnlyRootFilesystem: true`).                                            |
| H-8                     | HIGH     | `charts/lolday/templates/postgresql.yaml` — restricted securityContext (`runAsUser: 999`). `readOnlyRootFilesystem: false` (PG needs `/var/lib/postgresql/data` writable); add an `emptyDir` for `/tmp` since `readOnlyRootFilesystem` requires it.                                                                                                    |
| H-9                     | HIGH     | `charts/lolday/templates/redis.yaml` — restricted securityContext + `requirepass` (`REDIS_PASSWORD` env-backed Secret) + `--protected-mode yes`.                                                                                                                                                                                                       |
| H-10                    | HIGH     | `backend/Dockerfile` — `RUN useradd -m -u 1000 lolday && chown -R lolday:lolday /app` then `USER 1000`.                                                                                                                                                                                                                                                |
| H-11                    | HIGH     | `backend/app/services/build.py:83-89` — replace `seccompProfile: Unconfined` with a custom seccomp profile mirroring [BuildKit's example](https://github.com/moby/buildkit/blob/master/examples/kubernetes/buildkit-rootless-seccomp.json) (mounted via `localhostProfile`).                                                                           |
| H-12                    | HIGH     | New `charts/lolday/templates/lolday-default-deny-np.yaml` — `podSelector: {}` `policyTypes: [Ingress]` + per-service allow rules for Postgres/Redis/MinIO/Harbor/MLflow.                                                                                                                                                                               |
| H-13                    | HIGH     | Delete `charts/lolday/templates/network-policy.yaml::deny-training-egress` (orphan selector).                                                                                                                                                                                                                                                          |
| H-14                    | HIGH     | `lolday-jobs`, `monitoring`, `lolday` namespaces — add `pod-security.kubernetes.io/{enforce,audit,warn}` labels. `lolday-jobs` starts at `audit: restricted` + `enforce: baseline` for 7 days, then promotes to `enforce: restricted` (BuildKit moves to its own `lolday-builds` ns at `enforce: baseline` to retain the custom seccomp profile path). |
| H-15                    | HIGH     | New `charts/lolday/templates/mlflow-forward-auth-middleware.yaml` (Traefik) + sub-router in backend `routers/mlflow_authz.py` that maps `Cf-Access-Authenticated-User-Email` + per-experiment tags to allow/deny. MLflow chart values unchanged (no basic-auth plugin).                                                                                |
| H-16                    | HIGH     | `charts/lolday/templates/ingress.yaml:25-31` `/mlflow/` route — Traefik middleware method allowlist: `GET, HEAD, OPTIONS` for any SSO user; `POST, PATCH, DELETE` only for admin allowlist.                                                                                                                                                            |
| H-21                    | HIGH     | `backend/app/services/job_spec.py::build_volcano_job_manifest` — `spec.queue` always rendered server-side from the authenticated principal, never read from request body. Add an integration test.                                                                                                                                                     |
| M-backend-np            | MEDIUM   | New ingress NetworkPolicy on `backend` pod: allow only `cloudflared` (own ns) and `lolday-jobs` (callback paths).                                                                                                                                                                                                                                      |
| M-internal-split        | MEDIUM   | `/api/v1/internal/*` mounted on a sub-app instance bound to an extra container port `8001`; Service exposes both, NetworkPolicy gates `:8001` to `lolday-jobs` only. Cloudflared tunnel ingress maps `:8000` only.                                                                                                                                     |
| M-cloudflared-np        | MEDIUM   | `charts/lolday/templates/netpol-cloudflared.yaml` — add `policyTypes: [Ingress, Egress]`; ingress allowed only from `monitoring`.                                                                                                                                                                                                                      |
| M-minio-console         | MEDIUM   | `charts/lolday/values.yaml` MinIO — `consoleService.type: ""` (no Service); operator port-forwards on demand.                                                                                                                                                                                                                                          |
| M-alembic-hardening     | MEDIUM   | `charts/lolday/templates/alembic-upgrade-hook.yaml` — restricted securityContext + `automountServiceAccountToken: false`.                                                                                                                                                                                                                              |
| M-mlflow-init-hardening | MEDIUM   | `charts/lolday/templates/mlflow-db-init-job.yaml` — container-level securityContext (drop ALL, allowPrivEsc false, RuntimeDefault, readOnlyRootFilesystem true with `/tmp` emptyDir).                                                                                                                                                                  |

**Acceptance criteria:**

1. `kubectl describe ns lolday-jobs` shows `pod-security.kubernetes.io/enforce=restricted` after the 7-day observation window.
2. Start a debug pod in the `default` ns; `pg_isready -h lolday-postgresql.lolday.svc -p 5432` → connection refused (NetworkPolicy).
3. From a job pod in `lolday-jobs`: `curl -sS http://lolday-mlflow.lolday.svc:5000/api/2.0/mlflow/experiments/list` → 401 (ForwardAuth middleware).
4. `curl -X DELETE https://lolday/mlflow/api/2.0/mlflow/experiments/delete -H "..."` as a non-admin user → 403.
5. `kubectl exec` into the backend pod → `cat /var/run/secrets/kubernetes.io/serviceaccount/token` → file not present (`automountServiceAccountToken: false`).
6. Backend regression test: submit job → BuildKit container builds the detector image successfully (regression check on the custom seccomp profile).
7. `kubectl get pod -n lolday <postgresql-pod> -o jsonpath='{.spec.containers[0].securityContext}'` shows `runAsUser: 999, capabilities: {drop: [ALL]}, allowPrivilegeEscalation: false`.

### 6.3 P3 — Secret lifecycle closure (1 week)

**Goal:** Every secret in the platform has a defined rotation cadence, no
secret leaks into logs, and the operator can rotate keys without
re-issuing the entire data set.

| Finding               | Severity                 | Summary                                                                                                                                                                                                                                                                         |
| --------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- | -------------------- |
| H-17                  | HIGH                     | `backend/tests/conftest.py:3` — replace hardcoded test key with `Fernet.generate_key()` per-session. Add `Settings.validate_fernet_key` rejecting the legacy test value when `ENVIRONMENT == "production"`.                                                                     |
| H-18                  | HIGH                     | `backend/app/services/crypto.py` — `TokenCipher` switches to `MultiFernet([Fernet(k) for k in keys])`. Settings accept `FERNET_KEYS` (whitespace-separated, first = active for encrypt).                                                                                        |
| H-18a                 | HIGH                     | New `backend/app/scripts/rotate_fernet.py` — re-encrypts every `UserGitCredential.encrypted_token` from old key to new key. Run before retiring the old key.                                                                                                                    |
| H-19                  | HIGH                     | `backend/app/services/build.py:165-194` — replace inline `https://$U:$T@github.com/...` with `git -c credential.helper='!f() { echo username=$GIT_USER; echo password=$GIT_TOKEN; }; f' clone https://github.com/$REPO.git`. Tokens stay in env, never in argv.                 |
| H-22                  | HIGH                     | `.lolday-cloudflare-access-backups/` — runbook updated to require `age -r $RECIPIENT < state.json > state.json.age`. Existing cleartext snapshots deleted from operator workstation.                                                                                            |
| M-deploy-from-literal | MEDIUM                   | `scripts/deploy.sh:180-192` — replace `kubectl create secret --from-literal=webhook-url=$URL` with `tmpf=$(mktemp); chmod 600 $tmpf; printf %s "$URL" > $tmpf; kubectl create secret ... --from-file=webhook-url=$tmpf; shred -u $tmpf`. Match the `recover-harbor.sh` pattern. |
| M-discord-log         | MEDIUM                   | `backend/app/services/notify.py:43-45` — replace `logger.exception(...)` with `logger.warning("Discord notify failed: status=%s host=%s", resp.status_code, urlparse(url).hostname)`.                                                                                           |
| M-token-secret-owner  | MEDIUM                   | `backend/app/services/jobs_dispatch.py` — after `vcjob` is created, `patch` the `job-token-<id>` Secret to add `ownerReferences` pointing at the vcjob. Reconciler also sweeps `job-token-*` Secrets older than `JOB_TTL_SECONDS_AFTER_FINISHED` where the job is terminal.     |
| M-pg-exporter         | MEDIUM                   | `charts/lolday/templates/monitoring/postgres-exporter.yaml` — switch from `DATA_SOURCE_NAME` DSN string to `DATA_SOURCE_USER` + `DATA_SOURCE_PASS` + `DATA_SOURCE_URI` triples. Leave `sslmode=disable` as documented (no in-cluster TLS yet — tracked under §9 risk register). |
| L-harbor-robot-rotate | LOW (lifecycle-critical) | Force-rotate the current `robot$build-pusher` once; set `duration: 7776000` (90 d) henceforth. Add `reconciler/harbor_rotate.py` that runs quarterly.                                                                                                                           |
| L-minio-key-rotate    | LOW                      | Rotate MinIO svcacct AK/SK using `openssl rand -base64 30                                                                                                                                                                                                                       | tr -d '/+=' | head -c 40` charset. |

**Force-rotate procedure (operator runbook):**

```text
# T-0 = maintenance window start
1. cordon backend / job submissions: helm upgrade --set backend.acceptingJobs=false ...
   (new feature flag introduced in P3; rejects POST /jobs and POST /detectors/{id}/builds with 503)
2. wait for all in-flight builds and jobs to drain (≤ 20 min)
3. export old key: OLD=$(kubectl get secret backend-fernet -o jsonpath='{.data.FERNET_KEY}' | base64 -d)
4. generate new key: NEW=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
5. run rotate: uv run python -m app.scripts.rotate_fernet --old "$OLD" --new "$NEW"
   (re-encrypts all UserGitCredential rows in-place under a SAVEPOINT; aborts if any row fails)
6. helm upgrade with FERNET_KEYS="$NEW $OLD" (MultiFernet decrypts either, encrypts with $NEW)
7. uncordon: helm upgrade --set backend.acceptingJobs=true ...
8. T+24h: helm upgrade with FERNET_KEYS="$NEW" (retire old key)
```

The same procedure applies to subsequent rotations; the script + the
`MultiFernet` primitive make this repeatable.

**Acceptance criteria:**

1. `crypto.py` unit test: encrypt with key A, decrypt with `MultiFernet([B, A])`, then with `MultiFernet([B])` → fails.
2. `kubectl describe pod <build-pod>` shows no env var named `GIT_TOKEN` containing the PAT (only the `GIT_ASKPASS`-driven helper script).
3. Simulate Discord webhook 500: Loki search `discord.com/api/webhooks` → 0 hits.
4. After force-rotate window, `harbor.api` `GET /robots` shows `build-pusher.expires_at` ≤ now + 90 d.
5. `.lolday-cloudflare-access-backups/` contains only `.age` files; cleartext `.json` removed.

**Operator runbook:** [`docs/runbooks/p3-fernet-rotation.md`](../../runbooks/p3-fernet-rotation.md).

### 6.4 P4 — Supply chain pin & verify (2 weeks)

**Goal:** Make every byte that runs in the cluster traceable to a signed,
scanned, immutable artifact.

| Finding               | Severity | Summary                                                                                                                                                                                                                                                                            |
| --------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- | -------------------------------------------------------------------- |
| H-21-img              | HIGH     | Every prod image reference in `charts/lolday/values.yaml`, every `FROM` in `backend/Dockerfile` / `frontend/Dockerfile` / `charts/lolday/helpers/*/Dockerfile`: pinned via `@sha256:<digest>`. Dependabot `docker` ecosystem covers all of them (extend `.github/dependabot.yml`). |
| H-22                  | HIGH     | `.github/actions/docker-meta-build/action.yml` — append `aquasecurity/trivy-action@<sha>` with `severity: CRITICAL`, `exit-code: 1`. Add `anchore/sbom-action@<sha>` to attach SPDX.                                                                                               |
| H-23                  | HIGH     | Same composite — append `sigstore/cosign-installer@<sha>` + `cosign sign --yes --keyless ghcr.io/bolin8017/lolday-${image}@${digest}` using GHA OIDC.                                                                                                                              |
| H-23-cluster          | HIGH     | Install **Kyverno** as a sub-chart of `charts/lolday`. Add `verifyImages` policy pinning the workflow identity `https://github.com/bolin8017/lolday/.github/workflows/{images,helpers}.yml@refs/heads/main`. PSS enforcement (from P2) folded into Kyverno background scans.       |
| M-cache-poison        | MEDIUM   | `.github/actions/docker-meta-build/action.yml:45-46` `cache-to scope` → `${{ inputs.image }}-${{ github.ref_name }}`.                                                                                                                                                              |
| M-helper-hashes       | MEDIUM   | `charts/lolday/helpers/{build-helper,mlflow-server,pytorch-cu12-base}/Dockerfile` — `pip install --no-cache-dir --require-hashes -r requirements.txt`. Generate the requirements via `uv pip compile --generate-hashes`.                                                           |
| M-pytorch-bootstrap   | MEDIUM   | `charts/lolday/helpers/pytorch-cu12-base/Dockerfile:36` — replace `curl ... get-pip.py                                                                                                                                                                                             | python3.12`with`python3.12 -m ensurepip --upgrade`. |
| M-codecov-gate        | MEDIUM   | `.github/workflows/backend.yml:47-54` — add `if: github.event_name == 'push'                                                                                                                                                                                                       |                                                     | github.event.pull_request.head.repo.full_name == github.repository`. |
| M-trivy-cron          | MEDIUM   | New `.github/workflows/trivy-cron.yml` — weekly schedule. Scans the two Dependabot-excluded base images (`pytorch-cu12-base`, `mlflow-server` upstream); opens an issue on CRITICAL.                                                                                               |
| M-harbor-sha-validate | MEDIUM   | `scripts/build-helpers.sh:142` `harbor_has_tag` — `[["$sha" =~ ^[0-9a-f]{6,64}$]]                                                                                                                                                                                                  |                                                     | return 2`.                                                           |
| L-mlflow-user         | LOW      | `charts/lolday/helpers/mlflow-server/Dockerfile` — `USER 1000` + filesystem ownership fix.                                                                                                                                                                                         |

**Acceptance criteria:**

1. `git grep "image:" charts/lolday/values.yaml | grep -vE "@sha256:"` → empty (excluding template stub strings).
2. Push a PR that introduces an image with a CRITICAL CVE → `images.yml` job fails on the Trivy step.
3. `cosign verify --certificate-identity-regexp 'https://github.com/bolin8017/lolday/.*' ghcr.io/bolin8017/lolday-backend@sha256:...` → OK.
4. `kubectl apply -f manifest.yaml` with an unsigned `image:` → Kyverno admission rejects.
5. `pnpm audit --prod --json` → 0 high/critical.

### 6.5 P5 — Audit, observability & frontend hardening (1 week)

**Goal:** Every security-relevant event is observable, retained, and the
frontend ships with industry-standard hardening headers.

| Finding                  | Severity | Summary                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H-27                     | HIGH     | `backend/app/metrics.py` — `AUTH_FAILURE_TOTAL = Counter("lolday_auth_failure_total", labels=["reason"])`. Increment in every `cf_access.py` failure branch. New Alertmanager rule `LoldayAuthFailureSpike` firing at `rate > 0.5/s for 5m`.                                                                                                                                                                                                                                                                                                                              |
| M-audit-log              | MEDIUM   | New SQLAlchemy model `AuditLog(actor_id, action, target_type, target_id, before_jsonb, after_jsonb, ts)`. Alembic migration. Insert from `routers/admin.py` role-change paths, `routers/datasets.py::delete_dataset`, `routers/detectors.py::delete_detector`.                                                                                                                                                                                                                                                                                                            |
| M-ratelimit-metric       | MEDIUM   | `backend/app/services/rate_limit.py` — `RATE_LIMIT_HITS_TOTAL = Counter("lolday_rate_limit_hits_total", labels=["prefix"])`. Alertmanager rule firing at `rate > 1/s for 10m`.                                                                                                                                                                                                                                                                                                                                                                                            |
| M-jwt-email-pii          | MEDIUM   | `backend/app/auth/cf_access.py:204-216` — `redact_email(e: str) -> str` that returns `f"{e[0]}***@{e.split('@', 1)[1]}"`; apply before logging `claims_peek`.                                                                                                                                                                                                                                                                                                                                                                                                             |
| F-sourcemaps             | MEDIUM   | `frontend/vite.config.ts:23` — `sourcemap: "hidden"`. `frontend/Dockerfile:19` — `RUN find dist -name '*.map' -delete`. Upload maps to GHA artifact (per-build, retained 14 d).                                                                                                                                                                                                                                                                                                                                                                                           |
| F-csp-headers            | MEDIUM   | `frontend/nginx.conf` — extend CSP: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'; upgrade-insecure-requests`. Add `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()`. `Cross-Origin-Opener-Policy: same-origin`. `Cross-Origin-Resource-Policy: same-origin`. `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`. |
| L-cookie-attrs           | LOW      | `frontend/src/components/ui/sidebar.tsx:91` — `document.cookie = \`${name}=${state}; path=/; max-age=${age}; Secure; SameSite=Lax\``.                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| L-discord-alert          | LOW      | New Alertmanager rule `LoldayDiscordNotifyFailing` keyed on `rate(lolday_backend_errors_total{stage="discord_notify"}[10m]) > 0.1`.                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| L-event-broker-drops     | LOW      | `backend/app/services/events_tail.py:66-70` — `EVENT_BROKER_DROPS_TOTAL = Counter(...)`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| L-detector-desc-sanitize | LOW      | `backend/app/routers/detectors.py:144-152` — on register, strip `<script>`, `<iframe>`, and `[text](url)` link syntax from `description`.                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| L-team-domain-validator  | LOW      | `backend/app/config.py:103-120` — `CF_ACCESS_TEAM_DOMAIN` Pydantic `field_validator` enforcing `^[a-z0-9-]+(\.[a-z0-9-]+)+$`.                                                                                                                                                                                                                                                                                                                                                                                                                                             |

**Acceptance criteria:**

1. `audit_log` table migration applied; PATCH `/admin/users/<id>` writes a row. `kubectl exec postgresql -- psql ... -c 'SELECT * FROM audit_log ORDER BY ts DESC LIMIT 5'` shows it.
2. Submit 5 invalid JWTs in 10 s → `lolday_auth_failure_total{reason="invalid_signature"}` ≥ 5; alert fires within `for: 5m`.
3. `curl -I https://lolday/` shows the full set of security headers.
4. `find frontend/dist -name '*.map'` after `pnpm build && docker build` → empty inside the runtime image.

### 6.6 P6 — DoS, residual MEDIUM, LOW cleanup (1–2 weeks)

**Goal:** Close the long tail of hardening items and lift the platform's
DoS tolerance to a state where a single malicious user cannot degrade
service for others.

| Finding                   | Severity | Summary                                                                                                                                                                                                                                                                                                       |
| ------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H-26                      | HIGH     | `backend/app/routers/users_me.py` (or new `system.py`) — `/health` now uses `Depends(rate_limit_ip("health", 120, 60))`. `backend/app/db.py` — `create_async_engine(..., pool_size=20, max_overflow=30)`. Add a `/livez` for kubelet only on a different path (no rate limit) bound to `:8001` internal port. |
| M-mlflow-stream           | MEDIUM   | `routers/experiments_proxy.py:233-262` — `download_artifact` uses `httpx.AsyncClient.stream` + FastAPI `StreamingResponse`. Add `asyncio.Semaphore(8)` per backend pod.                                                                                                                                       |
| M-notify-semaphore        | MEDIUM   | `backend/app/services/notify.py` — module-level `_NOTIFY_SEM = asyncio.Semaphore(20)`; failing acquire increments `BACKEND_ERRORS{stage="discord_notify_dropped"}`.                                                                                                                                           |
| M-reconciler-limit        | MEDIUM   | `backend/app/reconciler/loop.py:55-78` — both `select(...).limit(200)`. Add `RECONCILER_SCAN_TRUNCATED_TOTAL = Counter(...)` and order by `id` for resumability.                                                                                                                                              |
| M-csrf                    | MEDIUM   | New `backend/app/middleware/csrf.py` — for state-changing methods, require either `Sec-Fetch-Site: same-origin` OR `Origin` matching `Host`. Applies to all `/api/v1` except `/api/v1/internal/*` (job-token-authed).                                                                                         |
| L-experiment-stats-lock   | LOW      | `routers/experiments_proxy.py:43-47` — `WeakValueDictionary`.                                                                                                                                                                                                                                                 |
| L-clone-bandwidth         | LOW      | `backend/app/services/build.py` — `git clone --filter=blob:limit=10m --depth=1 ...`.                                                                                                                                                                                                                          |
| L-frontend-pull-policy    | LOW      | `charts/lolday/templates/frontend.yaml:16` — `imagePullPolicy: Always` (paired with P4 digest pin).                                                                                                                                                                                                           |
| L-cloudflared-runas       | LOW      | `charts/lolday/templates/cloudflared.yaml` — `runAsUser: 65532`.                                                                                                                                                                                                                                              |
| L-monitoring-quota        | LOW      | New `charts/lolday/templates/monitoring/quota.yaml` — `ResourceQuota` capping pods, replicas, PVC.                                                                                                                                                                                                            |
| L-ws-origin-check         | LOW      | `frontend/src/hooks/useJobEvents.ts:91-104` — defense-in-depth `if (ev.origin !== window.origin) return`.                                                                                                                                                                                                     |
| L-localstorage-ns         | LOW      | All `localStorage` keys prefixed with `lolday.` (`frontend/src/components/ThemeProvider.tsx`, `RunsColumnPicker.tsx`, route handlers).                                                                                                                                                                        |
| L-window-location         | LOW      | `_authed.datasets._index.tsx:93`, `_authed.detectors._index.tsx:156`, `_authed.jobs._index.tsx:216` — `useNavigate()` instead of `window.location.href`.                                                                                                                                                      |
| L-location-replace-encode | LOW      | `_authed.runs.$expId.$runId.tsx:19` — `encodeURIComponent`.                                                                                                                                                                                                                                                   |
| L-registry-dead           | LOW      | Delete `charts/lolday/templates/registry.yaml` (Harbor replaced it).                                                                                                                                                                                                                                          |
| L-samples-hostpath        | LOW      | Documented as accepted tech debt in `docs/architecture.md` §10; no migration this phase.                                                                                                                                                                                                                      |
| L-promql-fstring          | LOW      | `backend/app/services/gpu_signal.py:222-224` — validate `JOB_NAMESPACE` matches `^[a-z0-9-]+$` at startup.                                                                                                                                                                                                    |
| L-validator-size          | LOW      | `backend/app/services/validator.py:28-37` — `git clone --filter=blob:limit=10m` already added in build path; apply same to validator.                                                                                                                                                                         |

**Acceptance criteria:**

1. Run 1000 concurrent invalid-JWT requests against `/api/v1/jobs` → backend stays up; `lolday_rate_limit_hits_total` reflects the cap.
2. Download a 500 MiB MLflow artifact while backend memory limit is 512 MiB → no OOMKill; backend memory stays bounded.
3. CSRF check: cross-origin `fetch('/api/v1/jobs', {method:'POST'})` from `http://evil` → 403 (Origin mismatch).
4. `helm lint charts/lolday` clean.
5. `pnpm audit --prod` clean.

## 7. Cross-cutting breaking-change inventory

The operator has authorized these one-shot cutovers (see §2). Each carries
a maintenance window or coordinated rollout:

| Change                                       | Window                                                                                                              | Recoverability                                                             |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| **Fernet key rotation** (P3)                 | One 30-min window; script-driven (`rotate_fernet.py`).                                                              | Reversible only by keeping `OLD` key around for 24 h before retiring.      |
| **Git PAT credential helper** (P3)           | One PR; existing in-flight build jobs must be drained first.                                                        | Reversing requires re-deploying the prior backend image.                   |
| **Harbor robot rotate** (P3)                 | One operator action; new robot's secret written to `harbor-push-cred` Secret in both namespaces; old robot revoked. | Reversible by restoring the old robot from Harbor backup within retention. |
| **PSS `enforce: restricted` promotion** (P2) | One Helm upgrade after a 7-day `audit:restricted` observation window.                                               | Reversible by patching the namespace labels back.                          |
| **Kyverno admission install** (P4)           | Helm install; policies start at `mode: audit`, promote to `mode: enforce` after 7 days.                             | Reversible via `helm uninstall kyverno`.                                   |
| **Image digest pin sweep** (P4)              | Single commit per Dockerfile/values change; Dependabot takes over after.                                            | Trivial revert per file.                                                   |

## 8. Open question decisions

The brainstorming session committed to the following without deferring to
plan-time:

1. **Kyverno over OPA Gatekeeper.** Kyverno's policy YAML is more
   approachable (no Rego), it has first-class K3s support, and the
   `verifyImages` rule covers our Cosign use case natively. OPA
   Gatekeeper would require Rego authoring + an external `cosign verify`
   sidecar pattern.
2. **MLflow internal authn via Traefik ForwardAuth, not `mlflow basic-auth` plugin.**
   The plugin would force us to rebuild and maintain a custom
   `mlflow-server` image and manage usernames inside MLflow's auth DB.
   ForwardAuth lets us reuse the existing Cf-Access JWT + job-token
   primitives the backend already understands. Cost: one new Traefik
   middleware resource + a `/api/v1/mlflow-authz` endpoint in backend.
3. **Audit log: dedicated SQLAlchemy table, not JSONB on User.** Pure
   audit data has different access patterns (append-only, time-series
   queries, never user-facing), different retention (longer than the
   user lifetime), and benefits from indexed `(actor_id, ts)` /
   `(target_type, target_id)` lookups. JSONB on User would mix concerns
   and lose those indexes.

## 9. Risks & mitigations

| Risk                                                                                      | Phase | Likelihood | Mitigation                                                                                                                           |
| ----------------------------------------------------------------------------------------- | ----- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| MLflow ForwardAuth latency adds >100 ms to UI loads                                       | P2    | M          | Backend `/mlflow-authz` returns 200/403 from in-memory tag cache (TTL 60s); Traefik middleware `authResponseHeaders` short-circuits. |
| `MultiFernet` rotation script crashes mid-batch                                           | P3    | L          | SAVEPOINT per row; abort on first failure leaves DB consistent. Re-runnable.                                                         |
| Kyverno `verifyImages` policy rejects ALL pods on day 1 (image signing not yet caught up) | P4    | M          | Start in `mode: audit` for 7 days; promote to `enforce` only after 100% of running pods pass audit.                                  |
| PSS `enforce: restricted` breaks a sub-chart that hasn't been hardened                    | P2    | M          | Per-namespace label staging (`audit` → `warn` → `enforce`) gives 14 days of fail-soft observation.                                   |
| Fernet rotation window overlaps an in-flight build                                        | P3    | L          | Cordon backend (P3 introduces `acceptingJobs` feature flag) before rotation; drain (≤ 20 min).                                       |
| Body-size middleware (P1) breaks legitimate dataset uploads                               | P1    | L          | Cap chosen at 12 MiB to leave headroom over the existing 10 MiB CSV check. Adjustable via `BODY_SIZE_MAX_BYTES` env.                 |
| Custom BuildKit seccomp profile blocks a future BuildKit upgrade                          | P2    | M          | Document the profile source URL + commit SHA in the profile filename; revisit at each BuildKit bump.                                 |
| `lolday-builds` ns split (introduced in P2 for BuildKit) doubles namespace count          | P2    | L          | One-time chart change. `add-nfs-dataset.md` / `deploy.md` updated.                                                                   |
| `audit_log` table grows unbounded                                                         | P5    | L          | Add `pg_partman` monthly partitioning + 365-day retention. Tracked as P5 sub-task.                                                   |

## 10. Finding catalogue (authoritative ledger)

Use the IDs below in commit messages, PR descriptions, and plan
section references. Severity and phase placement are baked in.

### CRITICAL (2)

- **C-1** — Backend Role `secrets/configmaps` in `lolday` ns. (`charts/lolday/templates/backend-rbac.yaml:22-27`). **P1**
- **C-2** — `backend/Dockerfile:7` `uv:latest`. **P1**

### HIGH (32)

API authorization (P1):

- **H-1** MLflow proxy: no per-user ACL (`routers/experiments_proxy.py` 5 endpoints). **P1**
- **H-2** MLflow proxy: path traversal on `download_artifact`. **P1**
- **H-3** `GET /api/v1/builds/{id}` flat alias bypasses detector ACL. **P1**
- **H-4** `clone_dataset` forces `visibility=PUBLIC`. **P1**
- **H-5** `params` deep-merge overwrites `mlflow.tracking_uri`. **P1**
- **H-6** Content-Disposition injection via dataset name. **P1**
- **H-20** `Job.token_hash` not cleared on terminal status. **P1**

Platform privilege (P2):

- **H-7** Backend pod `automountServiceAccountToken` default true + missing securityContext. **P2**
- **H-8** Postgres pod no securityContext, runs as root. **P2**
- **H-9** Redis pod no securityContext, no password, runs as root. **P2**
- **H-10** Backend Dockerfile no `USER`. **P2**
- **H-11** BuildKit `seccompProfile: Unconfined`. **P2**

Tenant isolation (P2):

- **H-12** No default-deny ingress NetworkPolicy on `lolday` ns. **P2**
- **H-13** `deny-training-egress` NetworkPolicy is orphaned. **P2**
- **H-14** No PSS labels on any namespace. **P2**
- **H-15** MLflow no authn from in-cluster job pods. **P2**
- **H-16** `/mlflow/` ingress no method restriction. **P2**
- **H-21** Volcano queue accepts client-supplied queue name. **P2**

Secrets (P3):

- **H-17** Hardcoded Fernet test key in `conftest.py`. **P3**
- **H-18** No `MultiFernet` rotation. **P3**
- **H-19** Git PAT URL-embedded in `git clone`. **P3**
- **H-22** `.lolday-cloudflare-access-backups/` unencrypted. **P3**

Supply chain (P4):

- **H-21-img** Prod images use `:tag`, not `@sha256:`. **P4**
- **H-22-scan** No Trivy/Grype in CI. **P4**
- **H-23** No Cosign signing. **P4**

Observability & DoS (P1/P5/P6):

- **H-24** No FastAPI body-size cap. **P1**
- **H-25** `/metrics` unauthenticated in cluster. **P1**
- **H-26** `/health` no rate limit; DB pool exhaustion possible. **P6**
- **H-27** No `lolday_auth_failure_total` counter. **P5**
- **H-28** `fast-uri ≤ 3.1.1` (GHSA-q3j6-qgpj-74h6, GHSA-v39h-62p7-jpjc). **P1**

### MEDIUM (~30)

P1 placement:

- **M-WS-backdoor** — WebSocket `X-Test-User-Email` test-mode gate.
- **M-PAT-charset** — `GitCredentialSet.token` regex.
- **M-event-dict** — `routers/internal.py` `event` Pydantic model.
- **M-ilike** — Escape `%`/`_` in search.
- **M-docs-prod** — `DOCS_ENABLED` prod default false.

P2 placement:

- **M-backend-np** — Backend ingress NetworkPolicy.
- **M-internal-split** — `/api/v1/internal/*` sub-app on separate port.
- **M-cloudflared-np** — cloudflared `:2000` ingress NP.
- **M-minio-console** — MinIO Console no Service.
- **M-alembic-hardening** — alembic Job restricted securityContext.
- **M-mlflow-init-hardening** — `mlflow-db-init-job` container-level securityContext.

P3 placement:

- **M-deploy-from-literal** — `kubectl create secret --from-file` pattern.
- **M-discord-log** — Redact webhook URL in notify failure logs.
- **M-token-secret-owner** — `job-token-*` Secret `ownerReferences`.
- **M-pg-exporter** — postgres-exporter individual env vars.

P4 placement:

- **M-cache-poison** — BuildKit GHA cache scope per-ref.
- **M-helper-hashes** — Helper Dockerfile `pip install --require-hashes`.
- **M-pytorch-bootstrap** — Replace `curl get-pip.py | python`.
- **M-codecov-gate** — Codecov action head-repo gate.
- **M-trivy-cron** — Weekly Trivy cron on Dependabot-excluded images.
- **M-harbor-sha-validate** — `harbor_has_tag` SHA charset check.

P5 placement:

- **M-audit-log** — `AuditLog` SQLAlchemy model + migration + insertion.
- **M-ratelimit-metric** — `lolday_rate_limit_hits_total`.
- **M-jwt-email-pii** — Redact email in JWT decode-fail log.
- **F-sourcemaps** — Strip `.map` from prod runtime image.
- **F-csp-headers** — Extend CSP + add Permissions-Policy / COOP / CORP / HSTS.

P6 placement:

- **M-mlflow-stream** — Streaming artifact proxy.
- **M-notify-semaphore** — `asyncio.Semaphore(20)` on Discord notify.
- **M-reconciler-limit** — `.limit(200)` on reconciler scans.
- **M-csrf** — CSRF middleware (Origin/Sec-Fetch-Site).

### LOW (~25, all P6 unless flagged P3/P5)

(IDs prefixed `L-`; see §6.6 for the full enumeration. P3: `L-harbor-robot-rotate`, `L-minio-key-rotate`. P5: `L-cookie-attrs`, `L-discord-alert`, `L-event-broker-drops`, `L-detector-desc-sanitize`, `L-team-domain-validator`.)

## 11. Acceptance gate for the program

The program is complete when:

1. All six phase plans are merged.
2. Each phase's acceptance criteria (§§6.1–6.6) verified in
   production deployment.
3. `pnpm audit --prod`, `uv pip audit` (or equivalent), `trivy image`
   (CRITICAL gate), `helm lint`, `pre-commit run --all-files`
   all clean.
4. A follow-up audit pass (lightweight, single-agent reconnaissance)
   confirms each finding is closed; remaining items moved to
   `docs/architecture.md` §10 tech-debt with explicit reasoning.
5. The `docs/postmortems/` directory gains a "what we learned"
   summary entry for the 2026-05-12 audit, anchored on the
   five root-cause themes.

## 12. Related specs

- `docs/superpowers/specs/2026-04-29-engineering-hygiene-design.md` —
  the pre-commit / lint / format baseline this program assumes.
- `docs/superpowers/specs/2026-04-30-github-actions-cicd-design.md` —
  the SHA-pinning discipline P4 builds on.
- `docs/superpowers/specs/2026-05-11-storage-architecture-redesign-design.md` —
  the MinIO layer P2/P3 reference for credential isolation patterns.
- `docs/superpowers/specs/2026-05-10-alerting-redesign-design.md` —
  the Captain Hook / Spidey Warnings split P5's auth-failure and
  rate-limit alerts plug into.
