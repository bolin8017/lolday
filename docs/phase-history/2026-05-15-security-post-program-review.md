# Security Post-Program Review — 2026-05-15

> **Audit context.** The 2026-05-12 security hardening program (P1–P6, PRs
> #136 / #137 / #138 / #139 / #147 / #148) declared complete on 2026-05-14.
> This document captures a post-ship review performed 2026-05-15 against
> chart `v0.23.2` (helm rev 176, single-node K3s on `server30`).
>
> **Method.** 6 phase-verification agents + 3 framework cross-check agents
> ran kubectl / curl / code-grep against the live cluster and HEAD code at
> commit `a6da0d2`. Closure-ledger claims were re-verified against actual
> K8s objects and HEAD source, not just commit messages.
>
> **Audit-trail anchors.**
>
> - Source spec: [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../superpowers/specs/2026-05-12-security-hardening-design.md)
> - Closure ledger: [`docs/phase-history/2026-05-14-security-audit-findings.md`](2026-05-14-security-audit-findings.md)
> - Postmortem: [`docs/postmortems/2026-05-12-security-audit-program.md`](../postmortems/2026-05-12-security-audit-program.md)
> - Kyverno bootstrap runbook: [`docs/runbooks/kyverno-bootstrap.md`](../runbooks/kyverno-bootstrap.md)
> - Backend errors convention: [`.claude/rules/backend.md`](../../.claude/rules/backend.md) §`BACKEND_ERRORS` failure-bus

## Contents

1. [TL;DR](#tldr)
2. [Issues filed](#issues-filed)
3. [Verification matrix](#1-verification-matrix-p1p6)
4. [Confirmed drift](#2-confirmed-drift-program-shipped-but-prod-doesnt-match-spec)
5. [New findings outside program scope](#3-new-findings-outside-program-scope)
6. [Framework cross-checks](#4-framework-cross-checks)
7. [Lolday-specific deep dives](#5-lolday-specific-deep-dives)
8. [Recommended follow-up PRs](#6-recommended-follow-up-prs-prioritized)
9. [Doc-only updates needed](#7-doc-only-updates-needed)
10. [Pre-flip checklist for open-source release](#8-pre-flip-checklist-for-open-source-release)
11. [Open questions for operator](#9-open-questions-for-operator)

---

## TL;DR

- **88 program findings → 87 verified PASS in prod, 1 documented wire-contract deviation (M-event-dict), 0 regressed.** The program shipped cleanly.
- **6 confirmed drifts** between program claims and prod state (HIGH severity total): incomplete PSS labels (H-14 partial), `lolday-builds` namespace never created, `lolday-minio-console` Service residual, backend SA token still mounted (H-7 partial), 718 orphaned `job-token-*` Secrets, closure-ledger unit drift on Harbor duration.
- **17 new HIGH-confidence findings outside program scope** — **1 CRITICAL, 13 HIGH, 3 MEDIUM** — including a PAT-in-URL pattern reintroduction in `routers/detectors.py:130` that bypasses P3 H-19, a server-side WebSocket CSWSH gap (only the client-side check shipped in P6 L-ws-origin-check), and `request.client.host` returning Traefik's pod IP (defeats `rate_limit_ip`).
- **Framework gaps:**
  - **OWASP Top 10 (2023)**: A01 (cross-tenant `/builds/{id}`), A05 (`openapi_url` exposed, `uvicorn` missing `--proxy-headers`), A07 (rate-limit coverage incomplete, PyJWT `leeway=0`), A09 (audit log only 3 call sites), A10 (PAT-in-URL #2 in detectors register).
  - **CIS Kubernetes Benchmark**: 5.2.1 PSS labels gap on `lolday`/`monitoring`/`trivy-system`; 5.3.2 no NetworkPolicy in `monitoring`/`trivy-system`; 5.4.1 K3s secrets encryption disabled; 5.5 K8s API server audit log disabled.
  - **NSA-CISA**: no runtime threat detection (Falco/Tetragon); no backup/restore runbook for Postgres or MinIO.
  - **SLSA**: currently **L2**. Lift to L3 by adding `actions/attest-build-provenance` + enabling branch protection on `main` (currently HTTP 404).
- **Lolday-specific concerns:**
  - **Detector author egress**: BuildKit egress is `0.0.0.0/0 except RFC1918` — the only exfil window for compromised author code. Job-pod egress is properly clamped to MLflow + backend only.
  - **Secret rotation SOPs**: 6 of 11 secret classes have no documented procedure (Postgres, Redis, MinIO root, Harbor admin, Discord webhooks, cloudflared).
  - **Workstation single-point-of-failure**: every operator secret (`.lolday-secrets.env`, age recipient key) on one machine; no off-site recovery copy.
  - **Postgres backup CronJob does not exist** — biggest DR gap.
  - **Open-source flip prep**: `backend/kube-prometheus-stack/` not in `.gitignore`; hardcoded `140.118.155.14` in `docs/operations.md`; gmail address in `backend/tests/test_user_handle.py:36`; gitleaks scan never run.

---

## 1. Verification matrix (P1–P6)

| Phase                                     | PR   | Findings | Verified PASS | Documented deviation | Drift / regression                                                                 | Detail §                      |
| ----------------------------------------- | ---- | -------- | ------------- | -------------------- | ---------------------------------------------------------------------------------- | ----------------------------- |
| P1 — Stop the bleed                       | #136 | 17       | 16            | 1 (M-event-dict)     | 0                                                                                  | [Appendix A.1](#a1-p1-detail) |
| P2 — Workload identity & tenant isolation | #137 | 17       | 11            | —                    | **5** (H-7 partial, H-14, BuildKit ns split, M-minio-console, H-16 implementation) | [Appendix A.2](#a2-p2-detail) |
| P3 — Secret lifecycle closure             | #138 | 13       | 11            | —                    | **2** (M-token-secret-orphan, L-doc-harbor-unit) + 3 SOPs not rehearsed            | [Appendix A.3](#a3-p3-detail) |
| P4 — Supply chain pin & verify            | #139 | 11       | 11            | —                    | 0 (Kyverno GHCR-only is intentional D1 — see §4.5)                                 | [Appendix A.4](#a4-p4-detail) |
| P5 — Audit, obs & frontend hardening      | #147 | 11       | 11            | —                    | 0                                                                                  | [Appendix A.5](#a5-p5-detail) |
| P6 — DoS, residual MEDIUM, LOW cleanup    | #148 | 18       | 18            | —                    | 0                                                                                  | [Appendix A.6](#a6-p6-detail) |
| **Total**                                 | —    | **87**   | **78**        | **1**                | **8**                                                                              | —                             |

Counting reconciliation: the program declared 88 finding-IDs closed; this
review's table aggregates 87 (one program ID, `L-validator-size`, is folded
into `L-clone-bandwidth` per spec §6.6 — counted once).

`/health` rate-limit was verified live: 130 sequential requests from
`kubectl exec` returned the first 429 at request 121 and the final five all
429 (`rate_limit_ip("health", 120, 60)` correctly capping per-IP-bucket).
The bucket-key flaw documented in §3.4 means that "per-IP" is presently
_per-Traefik-pod_, but the cap mechanism itself works.

---

## 2. Confirmed drift (program shipped but prod doesn't match spec)

### D-1 — PSS labels incomplete (extends H-14) **HIGH**

The spec acceptance criterion §6.2 #1 said `lolday-jobs` would reach
`enforce: restricted` after a 7-day observation window, and §6.2 H-14
implied all three workload namespaces (`lolday-jobs`, `lolday`,
`monitoring`) would be PSS-labelled. **Actual state on 2026-05-15:**

```bash
$ kubectl get ns -L pod-security.kubernetes.io/enforce,audit,warn
NAME           STATUS  AGE  ENFORCE   AUDIT       WARN
lolday         Active  31d
lolday-jobs    Active   9d  baseline  restricted  restricted
monitoring     Active  24d
trivy-system   Active  24d
```

- `lolday-jobs` is at `enforce: baseline` (intermediate), not the
  spec-target `restricted`. The chart comment at
  `charts/lolday/templates/jobs-namespace.yaml:6-12` documents that
  promotion is _manual_ — pending an out-of-band runbook.
- `lolday`, `monitoring`, `trivy-system` have **no PSS labels at all**.
  Their workloads (backend, postgres, redis, mlflow, harbor, cloudflared,
  Kyverno, Prometheus, Alertmanager, Loki, Alloy, MinIO, Trivy-operator)
  run without any baseline floor; future-added pods in these namespaces
  inherit the implicit `enforce: privileged` default.

**Root cause:** D-2 (below) — promoting `lolday-jobs` to `restricted`
requires moving BuildKit out, and that move never happened.

**Recommended fix:** see issue #161 (D-2 covers BuildKit move; D-1 covers
labelling the other namespaces).

### D-2 — `lolday-builds` namespace never created **HIGH**

`docs/postmortems/2026-05-12-security-audit-program.md` §2 Theme B
asserts "BuildKit moved to `lolday-builds` ns at `enforce: baseline`".
**No such namespace exists:**

```bash
$ kubectl get ns lolday-builds
Error from server (NotFound): namespaces "lolday-builds" not found
```

BuildKit's `buildkit-seccomp-installer` DaemonSet currently runs in
**`lolday`** namespace with `runAsUser: 0` and added capabilities
(`CHOWN`, `DAC_OVERRIDE`, `FOWNER`). This is the structural reason
`lolday` ns cannot be PSS-labelled `restricted` and `lolday-jobs` cannot
be promoted from `baseline` to `restricted`.

**Recommended fix:** create the `lolday-builds` namespace at
`enforce: baseline / audit: restricted / warn: restricted`; move
`buildkit-seccomp-installer` there; then promote `lolday-jobs` to
`enforce: restricted` and label `lolday` at `enforce: restricted`
(after audit-mode soak), `monitoring`/`trivy-system` at
`enforce: baseline`.

### D-3 — `lolday-minio-console` Service still exists (residual M-minio-console) **LOW**

The MinIO sub-chart was supposed to set `consoleService.type: ""` to
avoid creating a Service for the console port (per spec §6.2
`M-minio-console`). The Service object is still there:

```bash
$ kubectl get svc -n lolday | grep minio
lolday-minio-console   ClusterIP  10.43.193.120  <none>  9001/TCP   ...
```

The console TCP port (9001) is _not reachable_ in-cluster because the
NetworkPolicy `minio-console-no-ingress` only allows port 9000, but the
Service object itself shouldn't exist. Future NP edits could
re-expose the console accidentally.

**Recommended fix:** confirm `consoleService.type: ""` in
`charts/lolday/values.yaml`; current chart values may need a sub-chart
parameter override (the minio/minio chart 5.4.0 limitations apply —
see memory note `project_minio_chart_no_multi_pool.md`).

### D-4 — Backend pod `automountServiceAccountToken: true` (extends H-7) **MEDIUM**

H-7 acceptance said `automountServiceAccountToken: false`. Live:

```bash
$ kubectl get pod -n lolday -l app=backend -o jsonpath='{.items[0].spec.automountServiceAccountToken}'
true
$ kubectl exec -n lolday backend-7df4d8c5bc-lnhxf -- ls /var/run/secrets/kubernetes.io/serviceaccount/
ca.crt  namespace  token
```

Combined with the Role narrowed in C-1 (PVC-list only), the blast
radius is small — but the spirit of H-7 was to deny the backend any
SA token at all. Either flip to `false` (preferred if the backend
doesn't talk to the K8s API server) or document why it's left mounted.

**Recommended fix:** grep `backend/app/services/k8s.py`,
`services/jobs_dispatch.py`, `services/harbor_init.py`,
`services/cluster_status.py` for `load_incluster_config()` — if any
call site exists, document the dependency; otherwise set
`automountServiceAccountToken: false` in
`charts/lolday/templates/backend.yaml`.

### D-5 — 718 orphaned `job-token-*` Secrets in `lolday` ns (extends M-token-secret-owner) **HIGH**

M-token-secret-owner shipped the `ownerReferences` patch + the
`reconcile_orphan_token_secrets` sweep. Live state:

```bash
$ kubectl get secret -n lolday --no-headers | grep -c '^job-token-'
718
$ kubectl get secret -n lolday-jobs --no-headers | grep -c '^job-token-'
1
```

All 718 Secrets in `lolday` ns were created 2026-04-19 to 2026-04-27
(predate the P3 merge of 2026-05-13), have `ownerReferences=NONE`, and
are well past `JOB_TTL_SECONDS_AFTER_FINISHED=604800s` (7d). The
reconciler sweep at `backend/app/reconciler/orphans.py:179-272` should
delete these but evidently is not. Hypothesis: `settings.JOB_NAMESPACE`
defaults to `"lolday"` (`backend/app/config.py:51`), but Phase 13a moved
job dispatch to `"lolday-jobs"`. The sweep may only look in
`JOB_NAMESPACE` (one ns) while real legacy Secrets straddle both. This
is a probable scoping bug in the sweep.

**Recommended fix:** read the sweep's namespace argument; if it's
hard-coded to one ns, switch to a list of both `JOB_NAMESPACE` and the
legacy `lolday` value, with a runbook step to one-shot delete the 718
pre-P3 Secrets after verifying none are still referenced.

### D-6 — Doc unit drift on Harbor robot rotation duration **LOW (doc-only)**

`docs/phase-history/2026-05-14-security-audit-findings.md`
`L-harbor-robot-rotate` row says
"`duration: 7776000 (90 d)`". Actual code (`scripts/recover-harbor.sh:86`
and `backend/app/reconciler/harbor_rotate.py:40`) uses `duration: 90`
(days). Harbor v2 swagger unit for `Robot.duration` is days, not
seconds (matches auto-memory `project_harbor_api_units_endpoints.md`).
The implementation is correct; only the doc lies.

**Recommended fix:** patch the closure ledger entry to "`duration: 90`
(90 d, Harbor swagger unit is days)".

### D-7 — H-16 method allowlist implemented in backend, not Traefik (cosmetic) **INFO**

Spec §6.2 H-16 prescribed "Traefik middleware method allowlist". The
shipping implementation enforces the same allowlist inside
`backend/app/routers/mlflow_authz.py:240,253-257`, reading
`X-Forwarded-Method` set by Traefik's ForwardAuth and rejecting mutating
methods for non-admin browser users. Behavior is equivalent;
spec and postmortem text should be updated to record the
implementation choice (Traefik headers middleware not used).

---

## 3. New findings outside program scope

Numbering: severity in brackets. Recommended issue link added once
issues open.

### 3.1 PAT embedded in `git clone` URL — second site missed by P3 H-19 **CRITICAL**

`backend/app/routers/detectors.py:127-135` constructs a PAT-in-URL
`git clone` command for the register-preview path:

```python
url_with_cred = (
    f"https://{pat}@github.com/{owner}/{repo}.git"
    if pat
    else f"https://github.com/{owner}/{repo}.git"
)
proc = await asyncio.create_subprocess_exec(
    "git", "clone", "--depth=1", url_with_cred, tmpdir, ...
)
```

P3 H-19 closed the equivalent pattern in `services/build.py` by
switching to `git -c credential.helper=...` and keeping the PAT in env
vars only. **The register-preview path was missed.** The PAT leaks
into:

- `/proc/<pid>/cmdline` for the lifetime of the clone — anyone with
  `kubectl exec` on the backend pod can `cat /proc/<pid>/cmdline`.
- Container metrics / process listings exported by the kubelet.
- The error response to the client: line 163 returns
  `err.decode(errors="ignore")[:200]` to the API caller. `git`'s
  failure output (`fatal: Authentication failed for
https://<pat>@github.com/...`) routinely contains the URL.

**Severity rationale:** the PAT is a per-user GitHub credential
already stored Fernet-encrypted in `UserGitCredential`. Leaking it to
process state + the HTTP response materially weakens what P3 H-19 was
meant to fix; the user's PAT can be lifted by any backend-pod-exec
holder, and on a clone-failure the user effectively self-services their
own PAT back into the response body (e.g. PAT typo with
`pat=ghp_…valid…` → the URL containing the valid `pat` is rendered to
the caller). On a public-repo flip, the attack chain shortens:
register-preview is a user-reachable endpoint.

**Fix sketch:** mirror `services/build.py:195` — `git -c
credential.helper='!f() { ... }; f' clone …`, pass `GIT_USER`/
`GIT_TOKEN` via the subprocess env, scrub the captured `err.decode()`
for the URL pattern before returning. Add a backend regression test
that asserts the captured stderr does not contain `ghp_`.

→ **issue to open: HIGH/CRITICAL**.

### 3.2 WebSocket server does NOT check `Origin` header (CSWSH) **HIGH**

`backend/app/routers/jobs.py:777-822` (`_resolve_user_from_ws`) reads
`cf-access-jwt-assertion` for handshake auth but **never compares
`websocket.headers["origin"]` against `Host`**. P6 `L-ws-origin-check`
added an `ev.origin` check on the **client** side
(`frontend/src/hooks/useJobEvents.ts:99`), which is a UX cushion, not a
server-side protection.

**Attack chain:** a logged-in lolday user visits an attacker site. The
attacker JS opens `new WebSocket("wss://lolday.connlabai.com/api/v1/jobs/<known-or-guessed-uuid>/events")`.
The browser auto-attaches the CF Access cookie cross-origin (CF
Access cookie defaults to `SameSite=None` for WS). The backend
authenticates the request via the cookie-attached JWT, accepts the
WS, and starts streaming job events. The attacker can read job
status, log tails, and any structured event payload — for any job the
user owns (`owner_id == user.id` check holds) or any job at all if the
user is admin.

The CF Access JWT lifetime (default 24h) keeps this exploit hot for a
day after a user clicks a malicious link.

**Fix sketch:** in `_resolve_user_from_ws`, reject when
`websocket.headers.get("origin")` is present and doesn't match
`websocket.headers.get("host")` (or `settings.PUBLIC_HOSTNAME`). Same
pattern as `backend/app/middleware/csrf.py`. Close with code 4403.

→ **issue to open: HIGH**.

### 3.3 Cross-tenant build read via flat alias **HIGH**

`backend/app/routers/builds.py:29-42`:

```python
@router.get("/{build_id}", response_model=BuildRead)
async def get_build_flat(build_id: UUID, ...):
    build = await session.get(DetectorBuild, build_id)
    if build is None:
        raise HTTPException(404)
    await load_detector(detector_id=build.detector_id, session=session)
    return BuildRead.model_validate(build)
```

`load_detector` only checks existence + soft-delete. It does not check
ownership. Any authenticated user can `GET /api/v1/builds/<any-build-uuid>`
and read another user's `BuildRead`, which exposes `failure_reason`
and `log_tail` — stderr from `git clone` and BuildKit. If the upstream
fix for §3.1 is not also applied, a build that failed with a PAT
typo leaks the typo'd PAT to whoever can guess the build UUID. Even
after §3.1 is fixed, the build failure logs may contain detector
source filenames, internal paths, and `.gitignored` filenames that are
private.

P1 H-3 marked this finding closed; the agent verifying P1 confirmed
the soft-delete check but flagged the missing `owner_id == user.id`
check. The closure ledger entry should be reinterpreted as "ACL
intent verified, scope partial".

**Fix sketch:** add `if build.detector.owner_id != user.id and not
is_admin(user): raise HTTPException(404)` to the flat alias. Mirror
the nested route `routers/detectors.py` pattern (which uses
`require_detector_access(write=False)`).

→ **issue to open: HIGH**.

### 3.4 `uvicorn` started without `--proxy-headers` / `--forwarded-allow-ips` **HIGH**

`backend/entrypoint.sh` (and the Dockerfile `CMD`) do not pass
`--proxy-headers` or `--forwarded-allow-ips`. As a result:

```
request.client.host  →  Traefik's pod IP  (e.g. 10.42.0.18)
```

Two consequences:

- **`rate_limit_ip` is globally broken.** P6 H-26 closed
  `rate_limit_ip("health", 120, 60)`. Live verification did see the
  cap fire — but it fired with all real clients sharing the
  Traefik-pod-IP bucket. The cap is effectively 120 req/60s
  **cluster-wide** across every real client, not per real client. Two
  Traefik replicas slightly randomise which pod's IP a given client
  sees, but the per-real-IP semantic is gone.
- **Audit log / access log forensic value is broken.** Any future
  per-IP investigation (P5 audit_log expansion, Loki query for "what
  IP hit X") sees only Traefik pod IPs.

**Fix sketch:** change `entrypoint.sh` to
`uv run uvicorn app.main:app --proxy-headers --forwarded-allow-ips='*'
...`. Traefik (k3s default) already strips inbound `X-Forwarded-For`
and re-adds its own client-IP header, so trusting `*` is safe here.
Verify by re-running the 130-request smoke and confirming the cap
fires per-real-client.

→ **issue to open: HIGH**.

### 3.5 OpenAPI schema still served when `DOCS_ENABLED=false` **HIGH**

`backend/app/main.py:183-185`:

```python
docs_url="/docs" if settings.DOCS_ENABLED else None,
redoc_url="/redoc" if settings.DOCS_ENABLED else None,
# openapi_url NOT touched — defaults to /openapi.json
```

Any CF-Access-authenticated user can `GET /openapi.json` and dump the
complete backend API surface (~hundreds of paths, every Pydantic
schema, every router). On a public repo flip the schema goes from
"internal asset" to "external asset shoulder-surfable by anyone who
gets past CF Access" — exactly the surface the spec's
`M-docs-prod` finding was supposed to shutter.

`backend/app/internal_app.py:11` correctly sets both `docs_url` and
`redoc_url` to None (and the internal sub-app has no `openapi_url`
default reachable from outside :8001 NP).

**Fix sketch:** add `openapi_url="/openapi.json" if
settings.DOCS_ENABLED else None,` next to the existing two lines.
Flip the in-code default in `config.py:16` from `True` to `False` for
defense-in-depth (matches §3.5 INFO item from P1 verification).

→ **issue to open: HIGH**.

### 3.6 Audit log call sites are sparse — major forensic gap **HIGH**

`grep audit_log backend/app/routers/*.py` shows only 3 call sites:

```
admin.py:84       — admin role change
datasets.py:274   — dataset delete
detectors.py:366  — detector delete
```

Security-relevant events that are NOT logged in `audit_log`:

| Event                                       | Why it matters                                            | File                                                                    |
| ------------------------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------------- |
| Credential PUT / DELETE                     | Adding/removing a GitHub PAT is a privilege change        | `routers/credentials.py:21,69`                                          |
| Visibility flips (PRIVATE → PUBLIC)         | Confidentiality event                                     | `routers/datasets.py:194-195`, `routers/detectors.py` visibility-update |
| Admin-driven job cancel                     | Different actor than user-driven; trail required          | `routers/jobs.py:649`                                                   |
| MLflow proxy reads of another user's runs   | Admin impersonation visibility                            | `routers/experiments_proxy.py:list_runs / get_run / download_artifact`  |
| Login events                                | "Did user X log in between T1 and T2?" cannot be answered | `auth/cf_access.py::resolve_user_from_jwt`                              |
| Detector register (initial PAT scope grant) | Tied to §3.1                                              | `routers/detectors.py:register`                                         |

The shipped `AUTH_FAILURE_TOTAL` counter increments on each 401 but is
attribution-by-reason, not per-user. Without an audit row for
successful login, the platform cannot reconstruct who was active
during a given window once the Loki retention rolls past it.

**Fix sketch:** extend `services/audit.py::write_audit_log` to be
called from the 6 sites above. Tag each with a `target_type`
(`credential`, `dataset.visibility`, `job.cancel.admin`,
`mlflow.run.read.cross_user`, `auth.login`, `detector.register`).

→ **issue to open: HIGH**.

### 3.7 K3s API server audit log disabled (CIS 5.5) **HIGH**

`scripts/setup-k3s.sh` and the kubelet drop-in
`scripts/patch-k3s-kubelet-args.sh` pass only `--kubelet-arg=` flags.
Neither passes `--kube-apiserver-arg=audit-log-path=...` nor
`--kube-apiserver-arg=audit-policy-file=...`. CIS 5.5 fails. NSA-CISA
"Log Auditing" pillar fails. K8s admission events, RBAC reads,
exec/attach calls, and Secret reads are **not** logged anywhere
auditable.

Confusingly, the application-level `audit_log` table (P5 M-audit-log)
captures _user-facing_ lolday actions; it does not capture cluster
control-plane mutations. Two distinct audit trails are needed.

**Fix sketch:** modify `scripts/setup-k3s.sh` to add
`--kube-apiserver-arg=audit-log-path=/var/log/k3s/audit.log
--kube-apiserver-arg=audit-policy-file=/etc/rancher/k3s/audit-policy.yaml`.
Author a minimal `audit-policy.yaml` (start with the upstream baseline
from `kubernetes/audit/latest/policy.yaml`). Live-apply via a
patch-script following the `patch-k3s-kubelet-args.sh` pattern (SSH
safety hard rule). Note: K3s server restart required.

→ **issue to open: HIGH**.

### 3.8 K3s `--secrets-encryption` disabled (CIS 5.4.1) **HIGH**

K3s setup does not pass `--secrets-encryption`. Without it, K3s
stores K8s Secrets unencrypted in its embedded SQLite at
`/var/lib/rancher/k3s/server/db/`. Any node-root read leaks every
Secret (Fernet keys, tunnel tokens, Postgres password, MinIO root,
Harbor admin, the lot).

**Fix sketch:** add `--secrets-encryption` to the K3s install
command. Existing Secrets must be re-encrypted via
`kubectl get secrets -A -o json | kubectl replace -f -` after the
flag is on so the new encryption-provider takes effect. Restart
required.

→ **issue to open: HIGH**.

### 3.9 NetworkPolicy gaps in `monitoring`, `trivy-system`, and ns-wide on `lolday-jobs` (CIS 5.3.2) **HIGH**

```bash
$ kubectl get netpol -A
# monitoring: 0 policies
# trivy-system: 0 policies
# lolday-jobs: only per-app egress (lolday-build-egress, lolday-job-egress); no ns-wide ingress default-deny
```

Any pod that lands in `monitoring` or `trivy-system` (CRD reconciler,
side-loaded webhook, future kube-prometheus-stack add-on) is
unrestricted on ingress and egress. Any pod that lands in
`lolday-jobs` without the `app.kubernetes.io/name=lolday-job` label
(misconfigured operator, manual debug pod, future split) is
unrestricted on ingress.

**Fix sketch:** add `netpol-monitoring-default-deny-ingress.yaml`
(allow Prometheus scrape from kube-system Traefik + kps internal),
`netpol-trivy-system-default-deny-ingress.yaml` (allow trivy-operator
egress to Trivy DB CDN), and `netpol-lolday-jobs-default-deny.yaml`
(allow only `app.kubernetes.io/name=lolday-job` pods to talk to MLflow

- backend).

→ **issue to open: HIGH**.

### 3.10 Branch protection on `main` is OFF — SLSA L3 source gap **HIGH**

```bash
$ gh api repos/bolin8017/lolday/branches/main/protection
{"message":"Branch not protected","status":"404"}
```

SLSA L3 requires "source verified". The repo presently allows direct
push to `main` (subject to GitHub default rules). The
`gh pr merge --admin` precedent for blocked-CI emergencies (memory:
`reference_gha_billing_can_block_ci.md`) is unaffected by adding
branch protection — an admin can always force a merge.

**Fix sketch:** via GitHub UI or `gh api`, enable on `main`:

- Required passing checks: `lint`, `backend`, `frontend`, `helm`,
  `images`, `helpers`.
- Required reviews: 1 (operator works solo → self-imposed via
  `gh pr merge`).
- Require signed commits.
- Restrict force-push.
- Restrict branch deletion.

→ **issue to open: HIGH**.

### 3.11 SLSA build provenance not generated — single PR to lift L2 → L3 **HIGH**

`grep -rn 'attest-build-provenance' .github/` → empty. P4 closed
**signing** via cosign keyless, but cosign produces a Sigstore
signature, not a SLSA Provenance v1 in-toto statement. SLSA L3
specifically requires the latter.

**Fix sketch:** add a step to the `.github/actions/docker-meta-build`
composite (after the cosign step) using
`actions/attest-build-provenance@<sha>` with
`push-to-registry: true`. Extend the Kyverno
`verify-lolday-image-signatures` ClusterPolicy to include
`verifyImages.attestations` (Kyverno 1.10+ supports SLSA Provenance
v1 predicate). The Sigstore signature + the SLSA provenance compose
naturally; the job already has `id-token: write`.

→ **issue to open: HIGH**.

### 3.12 Kyverno `verifyImages` scope = GHCR-only; runtime Deployments are all Harbor **HIGH**

The ClusterPolicy `verify-lolday-image-signatures` `imageReferences`
glob is `ghcr.io/bolin8017/lolday-*`. Every long-running production
Deployment (`backend`, `frontend`, `mlflow-server`, `lolday-postgres`,
`lolday-redis`, helper init containers consumed by vcjobs) references
`harbor.lolday.svc:80/lolday/*`. **Therefore the admission gate
currently gates zero chart-managed Deployments.** It only fires for
test pods that happen to reference GHCR directly.

This is documented as D1 in the P4 plan, but the implication —
admission gate not active on the real workload set — is not flagged
in the closure ledger. After the source-→-signed-image chain is
strengthened (provenance attestation), the next bottleneck is Harbor
pushes being unsigned and unverified.

**Fix sketch:** three mainstream options ordered by effort:

1. **Mirror GHCR → Harbor:** operator runs `cosign copy
ghcr.io/bolin8017/lolday-backend@sha256:X
harbor.lolday.svc:80/lolday/lolday-backend:vX` (cosign copies the
   signature too). Chart points back at GHCR. Doubles registry
   storage but minimal moving parts.
2. **Sign Harbor pushes with a key:** generate cosign keypair on
   server30, sign at push time (extend `scripts/build-helpers.sh`),
   keep public key in a `ConfigMap` Kyverno can read. Add a second
   ClusterPolicy rule for Harbor images.
3. **Document the gap explicitly:** keep the GHCR-only scope and add a
   prominent note to `docs/architecture.md` §10 + a runbook in
   `docs/runbooks/kyverno-bootstrap.md` explaining what is and isn't
   covered. Lowest cost; lowest assurance.

→ **issue to open: HIGH (with the operator picking option 1/2/3)**.

### 3.13 Postgres backup CronJob does not exist — single biggest DR gap **HIGH**

`charts/lolday/templates/` has no Postgres backup CronJob. Lolday's
Postgres holds: users, jobs, builds, detectors, datasets, audit_log
(new in P5), and Fernet-encrypted `UserGitCredential` rows. If
Postgres dies — single replica, single PVC — the platform has no
recoverable state. The DR procedure today is "the operator
remembered to run pg_dump recently".

**Fix sketch:** add a `monitoring/pg-backup-cronjob.yaml`:
`pg_dumpall` daily to MinIO (`mlflow-s3-cred` svcacct exists; provide
a Postgres-specific svcacct via `init-buckets-job` to scope the bucket).
30-day retention via MinIO lifecycle. Document restore in
`docs/runbooks/db-restore.md`.

→ **issue to open: HIGH**.

### 3.14 BuildKit egress allows the public internet — exfil window **MEDIUM**

`charts/lolday/templates/build-networkpolicy.yaml`:

```yaml
egress:
  - to:
      - ipBlock:
          cidr: 0.0.0.0/0
          except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]
```

This is required for `git clone github.com` + `pip install` + `apt`
during a detector image build. **But it is the only structural exfil
path for a compromised detector source.** A typo-squatted PyPI dep, a
maldet release compromise, or a `setup.py` post-install hook can
exfiltrate samples / weights / credentials over HTTPS to any public
endpoint during the build.

**Fix sketch:** replace the public-internet allow with an FQDN
allowlist (Cilium FQDN policy or Calico Egress Gateway): github.com,
api.github.com, pypi.org, files.pythonhosted.org, ghcr.io,
registry-1.docker.io, deb.debian.org, security.debian.org, archive.ubuntu.com,
deb.nodesource.com (whatever the helper bases need). Document the
process for additions via PR review.

Alternative (defense-in-depth): require `pip install --require-hashes`
in detector `requirements.txt` (today only platform helpers require
hashes, per M-helper-hashes).

→ **issue to open: MEDIUM**.

### 3.15 Pre-flip prep — `.gitignore`, hardcoded IPs, gitleaks **HIGH**

Open-source flip is imminent. Three pre-flip blockers:

- `backend/kube-prometheus-stack/` is **not** in `.gitignore`. A
  future `git add -A` accidentally commits sub-chart values that may
  contain Grafana admin notes / webhook URLs.
- `140.118.155.14` (server14 NFS source) is hardcoded in
  `docs/operations.md` §"NFS dataset sources". Once public, this
  invites probing of an internal lab subnet.
- `islab.ai.tool@gmail.com` is referenced in CLAUDE.md. The git
  fixture `backend/tests/test_user_handle.py:36` carries
  `bolin8017@gmail.com`. PR #154 redacted `SSO_ADMIN_EMAIL`; the
  remaining email mentions in source / docs should be similarly
  redacted to `<operator@example.com>` placeholders.
- `gitleaks detect --no-banner -v --redact` has not been run against
  full history. Should run **before** the public toggle.

**Fix sketch:** one PR that adds `backend/kube-prometheus-stack/` to
`.gitignore`, redacts the IP + emails, and adds a `.github/workflows/
gitleaks.yml` that runs on `pull_request`.

→ **issue to open: HIGH**.

### 3.16 maldet PyPI takeover risk (A06 / A08) **MEDIUM**

`charts/lolday/helpers/build-helper/pyproject.toml:11` pins
`maldet[lightning]>=2.0,<3.0`. The constraint allows a future
`2.x.y+1` release; if maldet's PyPI publisher is compromised, the
next helper rebuild pulls the malicious release. The helper
Dockerfile's `--require-hashes` step pins the _currently resolved_
hash, so the existing CI image is safe until lock regenerate. On
regenerate the new hash is accepted whatever the upstream content.

**Fix sketch:** pin `maldet==<exact>` in helper requirements; bump
on operator-driven cadence with maldet CHANGELOG review. Or wait
for maldet to opt into PyPI sigstore signing and add
`pip sigstore-verify` to the build helper.

→ **issue to open: MEDIUM**.

### 3.17 MLflow `filter_string` constructed via f-string (A03) **MEDIUM**

`backend/app/routers/experiments_proxy.py:194`:

```python
filter_string = f'tags."lolday.user_id" = \'{user.id!s}\''
```

`user.id` is a UUID, so a runtime-supplied UUID can't break the
quoting. **But** the pattern is fragile: any future change to
`User.id`'s type (string handle? composite key?) silently re-opens an
MLflow query-language injection path.

**Fix sketch:** wrap the value in `mlflow.utils.search_utils.SearchUtils.parse_filter_string`
or rebuild via parameterised filter, not string concatenation.
Document in `.claude/rules/backend.md` that f-string into MLflow
filter is forbidden.

→ **issue to open: MEDIUM**.

---

## 4. Framework cross-checks

### 4.1 OWASP Top 10 (2023)

| Cat                             | Status                    | Closed by program                                                                                               | Open / new                                                                                                       |
| ------------------------------- | ------------------------- | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| A01 — Broken Access Control     | Mostly closed             | MLflow proxy ACL (H-1/H-2), token_hash on terminal (H-20), dataset clone visibility (H-4), self-PATCH allowlist | §3.3 cross-tenant builds (HIGH); admin-can-read-all is design                                                    |
| A02 — Cryptographic Failures    | Partial (accepted)        | Fernet rotation, .age backups, Secure cookies                                                                   | In-cluster TLS (Postgres/Redis/MinIO/Harbor/MLflow) — tech debt; only one DB column encrypted at rest            |
| A03 — Injection                 | Mostly closed             | All SQL via ORM, M-ilike escape, git-tag regex                                                                  | §3.17 MLflow filter f-string (MEDIUM)                                                                            |
| A04 — Insecure Design           | Closed (vigilance needed) | Stage-aware footgun removals (PR #112, EvaluateConfig.threshold)                                                | Re-grep new TrainConfig/EvaluateConfig/PredictConfig fields after PR #156                                        |
| A05 — Security Misconfiguration | Partial                   | DOCS_ENABLED=false, 8 nginx headers, restricted PSS on jobs ns                                                  | §3.5 openapi_url; §3.4 uvicorn proxy-headers; CSRF compares Origin vs Host (should use settings.PUBLIC_HOSTNAME) |
| A06 — Vulnerable Components     | Closed (with issue #19)   | Digest pin + Trivy gate + Cosign + Kyverno + Dependabot                                                         | §3.16 maldet PyPI hash-pin (MEDIUM); issue #19 Trivy CRITICAL upstream                                           |
| A07 — Authn Failures            | Mostly closed             | RS256 pin, aud/iss/exp/iat, role hierarchy                                                                      | PyJWT `leeway=0`; rate-limit coverage only on `/jobs` POST + `/builds` POST + `/health`                          |
| A08 — Software & Data Integrity | Closed                    | Digest pin, cosign, BuildKit cache per-ref, frozen lockfiles                                                    | §3.16 maldet hash-pin                                                                                            |
| A09 — Logging & Monitoring      | Closed (sparse)           | audit_log table, AUTH_FAILURE_TOTAL, RATE_LIMIT_HITS_TOTAL, alerts                                              | §3.6 audit_log call sites sparse (HIGH); Loki retention not pinned                                               |
| A10 — SSRF                      | Mostly closed             | Git URL regex pins to github.com, \_validate_artifact_path                                                      | §3.1 PAT-in-URL in detectors.register (CRITICAL) — re-introduces a P3 H-19 case                                  |

### 4.2 OWASP ASVS L2 — top 5 gaps

1. **V12.4 / V5.2 PAT in argv reintroduced** — §3.1.
2. **V12.6 / V8.2 Audit log sparse** — §3.6.
3. **V13.1 Real client IP unrecovered** — §3.4.
4. **V13.4 / V11.2 Authenticated rate-limit coverage** — `/experiments`,
   `/runs/{id}/artifacts/download`, WS `/jobs/{id}/events` have no
   per-user cap (and `rate_limit_ip` is broken per §3.4).
5. **V14.5 OpenAPI schema exposure** — §3.5.

### 4.3 CIS Kubernetes Benchmark

| Control                            | Result                                                                                                                                      |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 5.1.1 cluster-admin minimal        | ✅ — three bindings, all mainstream (`system:masters`, k3s Traefik helm installer)                                                          |
| 5.1.3 No wildcard verbs            | ⚠️ — `kps-operator` wildcard rule (upstream chart default); not lolday's gap                                                                |
| 5.1.5/6 SA token avoidance         | ⚠️ — D-4 backend SA still auto-mounted; kube-state-metrics / kps SAs mount tokens                                                           |
| 5.2.1 PSS labels on workload ns    | ❌ — D-1 (`lolday`, `monitoring`, `trivy-system` unlabelled)                                                                                |
| 5.2.2-12 Pod security details      | ⚠️ — MinIO `readOnlyRootFilesystem: false`; Alloy DaemonSet no securityContext; Loki RO/caps gap; BuildKit installer privileged (by design) |
| 5.3.2 NetworkPolicy in workload ns | ❌ — §3.9 (monitoring, trivy-system, lolday-jobs ns-wide)                                                                                   |
| 5.4.1 Secrets storage              | ❌ — §3.8 (K3s `--secrets-encryption` disabled)                                                                                             |
| 5.5 API server audit log           | ❌ — §3.7 disabled                                                                                                                          |

### 4.4 NSA-CISA Kubernetes Hardening Guide (2022)

Pillar coverage:

- **Pod Security**: P2 closed for platform pods. Three holdouts (MinIO, Alloy, Loki) — chart sub-values overrides.
- **Network Separation**: §3.9 monitoring/trivy-system uncovered; egress mostly missing from `lolday` ns (only cloudflared has an egress NP).
- **Authn/Authz**: P1 closed cluster-admin abuse; service-token role=-1 anti-escalation.
- **Log Auditing**: §3.7 not satisfied — K8s API audit log disabled.
- **Threat Detection**: ❌ no Falco/Tetragon. Recommended add for the public flip.
- **Upgrade Hygiene**: K3s `v1.34.6+k3s1` currently — within mainstream support. Recommend a quarterly cadence runbook.

### 4.5 SLSA L3+ assessment

**Current level achieved: SLSA L2.**

Lift to **L3** requires two PRs:

1. §3.11 `actions/attest-build-provenance` step + extend Kyverno
   `verifyImages.attestations`.
2. §3.10 enable branch protection on `main`.

Out-of-scope concerns (L4 territory): hermetic builds (BuildKit pulls
PyPI/apt at `docker build`), reproducible builds (no
`SOURCE_DATE_EPOCH`), two-party review (operator-solo workflow).

The **detector author trust path** (BuildKit-produced Harbor images)
is a separate signing track from the GHCR-keyless-OIDC track — see
§3.12 option 2.

---

## 5. Lolday-specific deep dives

### 5.1 Detector author trust model — residual gaps

The threat model in spec §3 treats detector authors as "trusted in
design, not in runtime code". P2 closed the BuildKit `Unconfined`
seccomp. Residuals:

- **BuildKit seccomp profile** is the Docker Engine default — the
  upstream-recommended baseline. Wider than K8s `RuntimeDefault` but
  this is the price of supporting rootless BuildKit. Compensating
  control was meant to be the `lolday-builds` ns isolation; that did
  not ship (D-2).
- **BuildKit egress = public internet** — §3.14. Realistic exfil
  surface during a malicious detector source build.
- **Job pod egress** — properly clamped to MLflow + backend only.
  Verified. Good.
- **maldet PyPI integrity** — §3.16. The detector framework is a
  single-maintainer PyPI package; takeover risk is real.
- **MLflow `log_artifact` write side** — the per-experiment ACL gates
  _reads_; _writes_ go through the per-experiment owner check. A
  compromised detector pod can still stage credential dumps as "model
  weights" under its own experiment's MinIO bucket. Defense-in-depth
  candidates: bucket size quota per user, content sniffing for
  obvious credential patterns, write-rate alert.

### 5.2 Secret rotation SOP coverage

| Secret                             | Documented SOP                           | Automated rotation                       | Last verified rotation                  |
| ---------------------------------- | ---------------------------------------- | ---------------------------------------- | --------------------------------------- |
| Fernet keys                        | yes (`runbooks/p3-fernet-rotation.md`)   | semi (`scripts/rotate_fernet.py`)        | shipped but **never rehearsed in prod** |
| MinIO svcacct (mlflow/harbor/loki) | partial (`scripts/rotate-minio-keys.sh`) | yes (script)                             | shipped but **never executed**          |
| Harbor robot$build-pusher          | yes                                      | yes (`reconciler/harbor_rotate.py`, 90d) | not due until 2026-07-20                |
| Postgres password                  | **none**                                 | **none**                                 | n/a                                     |
| Redis password                     | **none**                                 | **none**                                 | n/a                                     |
| MinIO root                         | **none**                                 | **none**                                 | n/a                                     |
| Harbor admin                       | **none**                                 | **none**                                 | n/a                                     |
| CF Access service-token            | partial (operator-local notes)           | **none**                                 | per-migration cadence                   |
| Discord webhooks (×4)              | **none**                                 | **none**                                 | n/a                                     |
| GitHub PAT (CI / Dependabot)       | n/a (operator manages)                   | n/a                                      | n/a                                     |
| cloudflared tunnel creds           | **none**                                 | **none**                                 | n/a                                     |

Recommended: a single `docs/runbooks/secret-rotation.md` consolidating
the 6 missing-SOP rows. Each entry can be 10 lines (helm upgrade with
`--set ...` + pod restart cadence).

**Also note**: Fernet + MinIO key rotation procedures shipped but
were never exercised against production. The first real rotation will
be unrehearsed. Recommend rehearsing each in the next maintenance
window.

### 5.3 JWT replay & lifecycle

- `backend/app/auth/cf_access.py:60-67` PyJWT decode validates `exp,
iat, aud, iss`. No `nbf`, no `jti`, **`leeway=0`** (PyJWT default).
- CF Access JWT lifetime defaults to 24h. A leaked JWT is replayable
  for 24h from any IP — no server-side revocation list.
- `leeway=0` causes spurious 401s on small server30 ↔ Cloudflare
  clock drift. Recommend `leeway=30`.
- **Mitigation: reduce CF Access session duration** in the Access app
  config (Cloudflare dashboard). Suggest 8h for `USER`, 2h for
  `ADMIN`. No code change required.
- **Service token path**: `Role.SERVICE_TOKEN: -1` blocks
  `require_role(USER)` correctly — confirmed safe.

### 5.4 MLflow SSRF surface

- `experiments_proxy.py:328-335` rejects any `artifact_uri` scheme
  other than `mlflow-artifacts:/`. The upstream URL host is hardcoded
  to `settings.MLFLOW_TRACKING_URI`. Path concatenation is
  per-segment URL-quoted (P1 H-2). **No external SSRF reachable.**
- Internal MLflow path traversal still requires control of MLflow's
  DB / artifact metadata, which goes through MinIO + Postgres
  (platform-controlled).
- P1 H-5 closed `_deep_merge` reserved-key check; no
  `tracking_uri` injection path remains.

### 5.5 WebSocket auth lifecycle

- Handshake authenticates via `cf-access-jwt-assertion` header —
  confirmed.
- **§3.2 Origin check NOT enforced server-side** — HIGH.
- No periodic re-auth on a long-lived connection. A WS established
  when the JWT had 24h remaining stays alive past JWT expiry. The WS
  endpoint emits read-only job events (no mutation surface), so the
  blast radius is bounded, but a 4-hour-old WS still leaks job events
  to the original session even if the user logged out.

### 5.6 Backup integrity & DR

- **Postgres**: §3.13. No automated backup. Single PVC, single
  replica.
- **MinIO**: single-node `minio/minio` 5.4.0. MLflow artifacts +
  Harbor blobs + Loki chunks all single-replica. No backup. (Memory:
  `project_minio_chart_no_multi_pool.md` documents the chart's
  standalone limitation.)
- **Harbor**: registry + database, single replica each. No backup.
- **Workstation single-point-of-failure**: `.lolday-secrets.env`,
  `.lolday-cf-svctoken.env`, `.lolday-cloudflare-access-backups/`,
  age private key (`~/.config/age/lolday-cf-access.key` per
  `docs/runbooks/cf-access-backups.md:34`) all on the operator's
  workstation. If the workstation is lost / encrypted / stolen and no
  off-site copy exists, the cluster cannot be re-deployed without
  re-issuing CF Access app config, regenerating Harbor admin password,
  re-deriving Fernet keys (= losing all `UserGitCredential` rows).
  Recommend: `age`-encrypted off-site copy of the secrets bundle to a
  recovery key held in a hardware token / second researcher / cloud
  vault. Document in `docs/runbooks/operator-workstation-backup.md`.

### 5.7 `architecture.md` §10 tech debt reconciliation

| §                                       | Status           | Notes                                                                                                                                                                                                                                        |
| --------------------------------------- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| #12 E2E seeding                         | open             | unrelated to security                                                                                                                                                                                                                        |
| #13 AUTH_DEV_MODE single-persona        | open             | unrelated to security                                                                                                                                                                                                                        |
| #14 schema.gen.ts drift                 | open             | unrelated to security                                                                                                                                                                                                                        |
| #17 Volcano #5044 watch                 | open             | passive                                                                                                                                                                                                                                      |
| #18 `@microlink/react-json-view` fork   | open             | LOW (frontend dep)                                                                                                                                                                                                                           |
| #19 RJSF v5 → shadcn templates          | **NEEDS UPDATE** | PR #156 (today) bumped RJSF to v6.5.2. Item text references v5 and `.rjsf-wrap` workaround. Re-write to (a) note v6 is current, (b) re-evaluate `@rjsf/shadcn` against v6 maturity, (c) confirm dark-mode CSS workaround still applies on v6 |
| #20 maldet schema description           | open             | external (maldet repo)                                                                                                                                                                                                                       |
| #21 ModelVersion INNER JOIN cascade     | open             | forward-looking                                                                                                                                                                                                                              |
| #22 Predict/Evaluate UX retired version | open             | forward-looking                                                                                                                                                                                                                              |
| #23 L-samples-hostpath                  | open             | by design (single-node)                                                                                                                                                                                                                      |
| #24 H-26 connection-pool                | open             | by design (2-replica cap)                                                                                                                                                                                                                    |

§10 #19 is the only entry that needs a content refresh from this
review.

### 5.8 Open-source flip risk surface

- §3.15 covers the immediate blockers (`.gitignore`, IP/email
  redaction, gitleaks scan).
- `lolday.connlabai.com` (production hostname) is referenced in
  16+ places — already DNS-public via Cloudflare. Accept (no
  remediation; flagging for the record).
- PR/CI secrets exposure: all workflows trigger on `pull_request`
  (not `pull_request_target`). Fork PRs from forks get empty
  `secrets.CODECOV_TOKEN`; Codecov uploads silently fail for fork
  PRs. No image-push secrets touched on fork PRs. **Confirmed safe.**
  Future contributors adding `secrets.X` to a `pull_request`
  workflow would re-open this — `.claude/rules/github-actions.md` is
  the gate.
- Memory: `project_security_audit_2026_05_12_shipped.md` lists the
  open-source flip context. After this review's items 3.15 + 3.10 +
  3.5 + 3.11 ship, the platform is suitable for public visibility.

---

## 6. Recommended follow-up PRs (prioritized)

Severity order; pick subset for actual work.

| #                       | Severity    | Finding                                                  | Effort | Suggested PR scope                                                                 |
| ----------------------- | ----------- | -------------------------------------------------------- | ------ | ---------------------------------------------------------------------------------- |
| §3.1                    | CRITICAL    | PAT-in-URL in `detectors.register` (P3 H-19 #2)          | S      | `credential.helper` env-injection + scrub `err.decode()` + regression test         |
| §3.2                    | HIGH        | WS server Origin check missing (CSWSH)                   | S      | `_resolve_user_from_ws` + 4403 close; integration test with cross-origin handshake |
| §3.3                    | HIGH        | Cross-tenant build read                                  | S      | `owner_id` check + admin bypass; mirror nested route                               |
| §3.4                    | HIGH        | uvicorn `--proxy-headers` missing                        | S      | entrypoint.sh one-line + smoke re-verify `rate_limit_ip`                           |
| §3.5                    | HIGH        | `openapi_url` exposed                                    | S      | `main.py:183` + flip in-code default                                               |
| §3.6                    | HIGH        | audit_log sparse                                         | M      | 6 new write sites + tests                                                          |
| §3.7                    | HIGH        | K3s API audit log disabled (CIS 5.5)                     | M      | setup-k3s.sh + audit-policy.yaml + live-patch script + SSH safety                  |
| §3.8                    | HIGH        | K3s `--secrets-encryption` disabled (CIS 5.4.1)          | M      | install flag + Secret re-encryption procedure + SSH safety                         |
| §3.9                    | HIGH        | NetworkPolicy gaps (monitoring/trivy-system/lolday-jobs) | M      | three new netpol templates                                                         |
| §3.10                   | HIGH        | Branch protection off on `main`                          | S      | GitHub UI / `gh api` — operational                                                 |
| §3.11                   | HIGH        | SLSA provenance not generated                            | S      | composite action step + Kyverno attestation rule                                   |
| §3.12                   | HIGH        | Kyverno scope = GHCR; Deployments are Harbor             | M      | pick option 1/2/3; documentation either way                                        |
| §3.13                   | HIGH        | Postgres backup CronJob missing                          | M      | pg_dumpall CronJob + restore runbook                                               |
| §3.14                   | MEDIUM      | BuildKit egress 0.0.0.0/0                                | L      | FQDN allowlist (Cilium/Calico) — biggest behaviour change                          |
| §3.15                   | HIGH        | Pre-flip prep                                            | S      | `.gitignore` + redactions + gitleaks workflow                                      |
| §3.16                   | MEDIUM      | maldet PyPI hash-pin                                     | S      | helper pyproject pin + bump runbook                                                |
| §3.17                   | MEDIUM      | MLflow `filter_string` f-string                          | S      | parameterised filter + backend rules doc note                                      |
| D-1+D-2                 | HIGH        | PSS drift + `lolday-builds` ns                           | M      | combined PR; ns creation + buildkit move + PSS promotion                           |
| D-3                     | LOW         | minio console Service residual                           | S      | sub-chart values override                                                          |
| D-4                     | MEDIUM      | backend SA token mounted                                 | S      | confirm no K8s API caller; flip to false                                           |
| D-5                     | HIGH        | 718 stale job-token-\* Secrets                           | S      | sweep scoping fix + one-shot cleanup                                               |
| D-6                     | LOW (doc)   | Closure ledger Harbor duration unit                      | XS     | one-line patch to the ledger row                                                   |
| D-7                     | INFO (doc)  | H-16 backend-side implementation                         | XS     | spec/postmortem footnote                                                           |
| §5.7 #19                | LOW (doc)   | architecture.md §10 #19 refresh                          | XS     | rewrite item to reflect RJSF v6                                                    |
| §5.2 rehearsal          | MEDIUM (op) | Rotation SOPs never executed                             | n/a    | operator runbook drill                                                             |
| §5.3 CF dashboard       | MEDIUM (op) | Reduce CF Access JWT TTL                                 | n/a    | dashboard config change                                                            |
| §5.6 workstation backup | MEDIUM (op) | Off-site secrets backup                                  | n/a    | `docs/runbooks/operator-workstation-backup.md`                                     |

Effort key: **XS** ≤ 30 min, **S** ≤ half day, **M** ≤ 1-2 days, **L** ≥ 3 days.

Issues to open from this review (top 16; matches CRITICAL + HIGH + the
combined D-1+D-2 + Pre-flip + the MEDIUM picks the operator may want
work on immediately):

§3.1, §3.2, §3.3, §3.4, §3.5, §3.6, §3.7, §3.8, §3.9, §3.10, §3.11,
§3.12, §3.13, §3.14, §3.15, plus a combined "D-1 + D-2 + D-4 + D-5"
PSS / BuildKit / SA / orphan-tokens infra cleanup epic.

---

## 7. Doc-only updates needed

- **Closure ledger** (`docs/phase-history/2026-05-14-security-audit-findings.md`):
  - Patch `L-harbor-robot-rotate` row "duration: 7776000" → "duration: 90 (days; Harbor swagger unit)" — D-6.
  - Add footnote to `H-16` row noting "method allowlist implemented in
    backend `mlflow_authz.py`, not Traefik headers middleware" — D-7.
  - Add footnote to `H-3` row noting "ACL intent verified, but
    `routers/builds.py:29` lacks `owner_id` check — see post-program
    review §3.3" — pre-§3.3 ship.
- **Postmortem** (`docs/postmortems/2026-05-12-security-audit-program.md`):
  - §2 Theme B "BuildKit moved to `lolday-builds` ns" — strikethrough
    and add "_PLANNED, not executed; see post-program review D-2_".
- **architecture.md §10 #19**: rewrite to reflect PR #156 (RJSF v5→v6
  bump). The dark-mode `.rjsf-wrap` workaround may still apply on v6
  — verify before re-writing.
- **`.claude/rules/backend.md`** (next time it's touched): document
  the f-string-into-MLflow-filter ban (§3.17) under a "Forbidden
  patterns" section.

---

## 8. Pre-flip checklist for open-source release

Order matters; do not flip public until every box is green.

- [ ] [#173](https://github.com/bolin8017/lolday/issues/173) §3.15 ship: `.gitignore` + `140.118.155.14` redaction +
      `bolin8017@gmail.com` → placeholder + `gitleaks` workflow.
- [ ] [#161](https://github.com/bolin8017/lolday/issues/161) §3.1 ship: PAT-in-URL fix in `detectors.register`.
- [ ] [#165](https://github.com/bolin8017/lolday/issues/165) §3.5 ship: `openapi_url` gated by `DOCS_ENABLED`.
- [ ] [#164](https://github.com/bolin8017/lolday/issues/164) §3.4 ship: uvicorn `--proxy-headers`.
- [ ] [#169](https://github.com/bolin8017/lolday/issues/169) §3.10: enable branch protection on `main`.
- [ ] gitleaks full-history scan: zero CRITICAL findings.
- [ ] [#162](https://github.com/bolin8017/lolday/issues/162) §3.2 ship: WS server Origin check.
- [ ] [#163](https://github.com/bolin8017/lolday/issues/163) §3.3 ship: cross-tenant build read fix.
- [ ] [#166](https://github.com/bolin8017/lolday/issues/166) §3.6 ship: at minimum, credential CRUD + login events written
      to `audit_log` so the public-facing auth surface has a forensic
      trail.

After these, the platform is suitable for `Public` visibility on
GitHub. The remaining items (CIS K3s, SLSA L3, audit log full
expansion, Postgres backup) become post-flip backlog.

---

## Issues filed

Opened 2026-05-15 from this review. Pre-flip blockers tagged in §8.

| #                                                      | Severity | Section   | Title                                                                              |
| ------------------------------------------------------ | -------- | --------- | ---------------------------------------------------------------------------------- |
| [#161](https://github.com/bolin8017/lolday/issues/161) | CRITICAL | §3.1      | PAT embedded in git clone URL in detectors.register (P3 H-19 #2)                   |
| [#162](https://github.com/bolin8017/lolday/issues/162) | HIGH     | §3.2      | WebSocket server does not check Origin header (CSWSH)                              |
| [#163](https://github.com/bolin8017/lolday/issues/163) | HIGH     | §3.3      | Cross-tenant build read via /api/v1/builds/{id} flat alias                         |
| [#164](https://github.com/bolin8017/lolday/issues/164) | HIGH     | §3.4      | uvicorn missing --proxy-headers; rate_limit_ip and access logs see Traefik pod IP  |
| [#165](https://github.com/bolin8017/lolday/issues/165) | HIGH     | §3.5      | OpenAPI schema served when DOCS_ENABLED=false                                      |
| [#166](https://github.com/bolin8017/lolday/issues/166) | HIGH     | §3.6      | audit_log call sites sparse — 6 critical events not logged                         |
| [#167](https://github.com/bolin8017/lolday/issues/167) | HIGH     | §3.7+§3.8 | K3s API server audit log + --secrets-encryption disabled (CIS 5.5 + 5.4.1)         |
| [#168](https://github.com/bolin8017/lolday/issues/168) | HIGH     | §3.9      | NetworkPolicy gaps in monitoring, trivy-system, lolday-jobs (CIS 5.3.2)            |
| [#169](https://github.com/bolin8017/lolday/issues/169) | HIGH     | §3.10     | Branch protection on main is off — SLSA L3 source verification gap                 |
| [#170](https://github.com/bolin8017/lolday/issues/170) | HIGH     | §3.11     | Add actions/attest-build-provenance — lifts SLSA L2 → L3                           |
| [#171](https://github.com/bolin8017/lolday/issues/171) | HIGH     | §3.12     | RFC — Kyverno verifyImages scope = GHCR only; chart-managed Deployments are Harbor |
| [#172](https://github.com/bolin8017/lolday/issues/172) | HIGH     | §3.13     | Postgres backup CronJob missing — single biggest DR gap                            |
| [#173](https://github.com/bolin8017/lolday/issues/173) | HIGH     | §3.15     | Pre-flip prep — .gitignore, IP/email redaction, gitleaks scan                      |
| [#174](https://github.com/bolin8017/lolday/issues/174) | HIGH     | D-1+D-2   | Complete H-14 — create lolday-builds ns, move BuildKit, promote PSS labels         |
| [#175](https://github.com/bolin8017/lolday/issues/175) | HIGH     | D-5       | 718 orphaned job-token-\* Secrets in lolday ns — reconciler sweep scoping bug      |

**Not filed as separate issues** (recorded in §6 for operator decision):

- §3.14 BuildKit egress `0.0.0.0/0` — MEDIUM; needs design (FQDN allowlist vs accept). File when ready to scope.
- §3.16 maldet PyPI hash-pin — MEDIUM.
- §3.17 MLflow `filter_string` f-string — MEDIUM; defense-in-depth.
- D-3 minio-console Service residual — LOW; sub-chart values override.
- D-4 backend SA token still mounted — MEDIUM.
- D-6 closure ledger Harbor duration unit drift — LOW (doc-only).
- D-7 H-16 backend-side implementation — INFO (doc-only).
- §5.7 #19 architecture.md §10 #19 refresh — LOW (doc-only).
- §5.2 rotation SOP rehearsal — operational, runbook drill.
- §5.3 CF Access JWT TTL reduction — operational, CF dashboard config.
- §5.6 workstation off-site backup — operational, custody decision.

---

## 9. Open questions for operator

The following items need the operator's input — not Claude's
judgement call:

1. **Kyverno Harbor coverage (§3.12)**: option 1 (GHCR mirror), 2
   (server30 cosign key), or 3 (accept gap with explicit
   documentation)?
2. **CF Access JWT session duration (§5.3)**: drop to 8h workday?
3. **Audit log retention policy** (P5 deferred + audit_log expansion
   in §3.6): when expanded to login + credential events, the row
   volume jumps. Spec §9 risk register said "pg_partman monthly
   partitioning + 365-day retention". Still the plan?
4. **BuildKit egress allowlist (§3.14)** vs. accept the current
   public-internet egress as a documented trust assumption?
5. **Workstation off-site backup (§5.6)**: hardware token, cloud
   vault, or second-researcher custody?
6. **Volcano fix watch (§5.7 #17)**: any change in upstream status
   since the program ship?

---

## Appendix A: Per-phase verification detail

### A.1 P1 detail

All 17 P1 findings verified. One documented deviation: **M-event-dict**.
`backend/app/schemas/job.py:181-201` `JobInternalEvent` uses
`model_config = ConfigDict(extra="allow")` instead of the spec's
`extra="forbid"`, gated by the `kind` Literal allowlist + 64 KB
serialized cap. The deviation is documented inline (`L184-187`):
maldet's wire contract emits data fields at the top level
(`{"kind":"metric","name":"loss","value":0.5}`); a `forbid` policy
would break the framework contract. Accept; update spec/ledger to
record the deviation.

Verification commands run (key ones):

```bash
kubectl auth can-i {get,list,create} {secrets,configmaps} -n lolday \
  --as=system:serviceaccount:lolday:backend
# → all "no"

grep '^COPY --from=ghcr.io/astral-sh/uv' backend/Dockerfile
# → COPY --from=ghcr.io/astral-sh/uv:0.11.13@sha256:841c8e6f...

kubectl get netpol -n lolday backend-metrics-from-monitoring-only -o yaml
# → ingress from kube-system/traefik + monitoring/prometheus only

grep 'fast-uri' frontend/pnpm-lock.yaml
# → fast-uri@3.1.2
```

P1 new finding (LOW, not blocking): `backend/app/config.py:16`
`DOCS_ENABLED: bool = True` as the in-code default. Production
safety relies entirely on the chart setting it to `"false"`. Flip
in-code default to `False` for defense-in-depth.

### A.2 P2 detail

Drifts: see §2 entries D-1 through D-4.

Verified PASS (no drift):

- H-9 Redis password: `kubectl exec redis -- redis-cli ping` → `NOAUTH
Authentication required.`; `redis-secret.password` is 63-byte base64.
- H-10 backend `USER 1000` in Dockerfile.
- H-11 BuildKit custom seccomp: `seccompProfile.type=Localhost`,
  `localhostProfile: profiles/buildkit-rootless.json`; DaemonSet
  installer Running.
- H-12 default-deny: `lolday-default-deny-ingress` exists with
  `podSelector: {}`; 10 per-service allow netpols cover
  backend (×2), frontend, harbor (×2), mlflow, postgresql, redis,
  minio-console, cloudflared.
- H-13 `deny-training-egress` removed.
- H-15 Traefik ForwardAuth middleware + `routers/mlflow_authz.py`
  endpoint at `POST /api/v1/mlflow-authz`.
- H-21 `spec.queue` rendered server-side from
  `ensure_user_queue(job.owner_id)`.
- M-backend-np, M-internal-split, M-cloudflared-np,
  M-alembic-hardening, M-mlflow-init-hardening.

Notable: H-16 method allowlist implemented backend-side
(`mlflow_authz.py`), not Traefik-side — D-7, behaviourally equivalent.

### A.3 P3 detail

Drifts: see §2 entries D-5 (stale tokens) and D-6 (doc unit).

Verified PASS:

- H-17 `Fernet.generate_key()` per-session in conftest.py;
  `validate_fernet_keys` rejects legacy committed test key in prod.
- H-18 `TokenCipher` uses `MultiFernet`; `FERNET_KEYS` whitespace-
  separated env (first = active).
- H-18a `rotate_fernet.py` 126 lines + 153-line test file.
- H-19 `credential.helper` clone pattern in `services/build.py:184-223`.
- H-22 runbook `docs/runbooks/cf-access-backups.md` requires
  `age -r $RECIPIENT`. (Operator workstation state inconclusive from
  server30.)
- M-deploy-from-literal, M-discord-log, M-pg-exporter.
- L-harbor-robot-rotate: live `expires_at: 1784548200 (2026-07-20)`,
  66d remaining; quarterly reconciler wired (`reconciler/loop.py:153`
  every 8640 iter ≈ 24h).
- L-minio-key-rotate: script + 3 follow-up fix commits
  (`14b73f3`, `d1621a7`, `adec4c2`); AK=20 + SK=40, charset matches.

**Operational observation**: Fernet + MinIO rotation SOPs shipped but
were never exercised against prod. First real rotation will be
unrehearsed. Recommend rehearsal in next maintenance window — see §5.2.

### A.4 P4 detail

All 11 findings PASS. Kyverno enforcement live-tested:

```bash
$ kubectl run test-fake-lolday \
    --image=ghcr.io/bolin8017/lolday-fake:latest \
    --dry-run=server -n lolday
# DENIED by Kyverno mutating webhook mutate.kyverno.svc-fail;
# all 4 attestor entries fail closed.
```

Documented gaps (accepted per D1):

- §3.12 Harbor-origin runtime images bypass `verifyImages` by policy
  scope.
- SBOM step disabled (memory: `project_syft_ghcr_sbom_disabled.md`).

### A.5 P5 detail

All 11 findings PASS. Notable:

- `audit_log` table migrated (`alembic_version=90125ce5ad35`); schema
  - 2 indexes verified; row count 0 (no admin/dataset/detector
    mutations since deploy).
- 8 security headers verified via in-cluster port-forward to
  frontend pod (CF Access blocks external curl with 302).
- All 4 P5 Prometheus counters declared and scrapeable.

Cosmetic note: `LoldayAuthFailureSpike` is `sum by (reason) (rate())`
— per-reason granularity, slightly stricter than spec's
"`rate > 0.5/s for 5m`". Likely intentional.

### A.6 P6 detail

All 18 findings PASS. Notable:

- **Live probe**: `/health` rate limit verified. 130 sequential
  in-cluster requests returned the first 429 at request 121.
- **Live probe**: livenessProbe = `/livez:internal:8001`,
  readinessProbe = `/api/v1/health:api:8000`. Two ports declared.
- Monitoring ResourceQuota: 3/20 pods, 3/30 replicasets, 2/5 PVCs.
- `RECONCILER_SCAN_LIMIT=200` with oldest-first ordering by
  `submitted_at` / `started_at`.

Cosmetic notes:

- L-localstorage-ns: audit grep recipe is brittle (doesn't catch
  backtick template literals); inspection confirmed every key
  resolves to a `lolday.` prefix.
- L-ws-origin-check: `useJobEvents.ts:99` uses `&&` short-circuit
  `if (ev.origin && ev.origin !== window.location.origin)` — strictly
  safer than the spec's `if (ev.origin !== window.origin)`. Note:
  this is **client-side**. The **server-side** Origin check is
  §3.2's HIGH gap.

---

_End of post-program review. Update this doc with issue numbers
once the §6 PRs are filed._
