# Security Hardening P5 — Audit, Observability & Frontend Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every security-relevant event observable and retained — auth failures, rate-limit hits, audit-worthy admin/destructive actions, event-broker drops — and ship the frontend with industry-standard hardening headers and a runtime image that never carries source maps.

**Architecture:** Eleven tasks across five chains. The **observability chain (T1–T4)** plants four Prometheus counters and two Alertmanager rules: `lolday_auth_failure_total{reason}` with a `LoldayAuthFailureSpike` rule fed from `cf_access.py`, `lolday_rate_limit_hits_total{prefix}` with a `LoldayRateLimitSpike` rule fed from `rate_limit.py`, `lolday_event_broker_drops_total` fed from `events_tail.EventBroker.publish`, and the `LoldayDiscordNotifyFailing` rule keyed off the existing `lolday_backend_errors_total{stage="discord_notify"}` (no new counter). The **audit chain (T5)** introduces a single `AuditLog` SQLAlchemy model + Alembic migration + a small `services/audit.py` writer with three call-site insertions (admin role-change, dataset soft-delete, detector soft-delete). The **PII redaction chain (T6)** adds `redact_email()` to `cf_access.py` and applies it inside the `claims_peek` log site so the existing JWT-invalid log no longer prints raw user mail. The **frontend hardening chain (T7–T9)** rewrites the nginx CSP block with a full hardening header set, switches Vite to `sourcemap: "hidden"` and strips `.map` files from the runtime image while uploading them as a per-build GHA artifact (14 d), and adds `Secure; SameSite=Lax` to the only client-set cookie (sidebar state). The **input validation chain (T10–T11)** strips raw HTML / Markdown link syntax from detector descriptions at registration and enforces a hostname-shape regex on `CF_ACCESS_TEAM_DOMAIN` via a Pydantic `field_validator`.

**Tech Stack:** SQLAlchemy 2.0 async + asyncpg (Postgres) / aiosqlite (tests), Alembic, `prometheus_client` Counter, kube-prometheus-stack `PrometheusRule` CRD, Alertmanager severity routing (existing Captain Hook / Spidey Warnings split per [`docs/superpowers/specs/2026-05-10-alerting-redesign-design.md`](../specs/2026-05-10-alerting-redesign-design.md)), Pydantic v2 `field_validator`, Vite 5 build pipeline, `nginxinc/nginx-unprivileged` 1.29, GitHub Actions `actions/upload-artifact@v4`.

**Source spec:** [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../specs/2026-05-12-security-hardening-design.md) §6.5.

**Finding IDs covered:** H-27, M-audit-log, M-ratelimit-metric, M-jwt-email-pii, F-sourcemaps, F-csp-headers, L-cookie-attrs, L-discord-alert, L-event-broker-drops, L-detector-desc-sanitize, L-team-domain-validator (11 spec findings, 11 implementation tasks).

---

## Design decisions (resolved up-front)

The implementer should not re-litigate these; they are locked.

**D1 — `AuditLog.before_jsonb` / `after_jsonb` granularity.** Cherry-picked per call-site, NOT full ORM row dumps and NOT diffs. Each insertion writes a small `dict[str, Any]` with the 2–4 fields that identify the resource and the actual state delta. Rationale: (a) full ORM dumps couple the audit schema to the current model — adding a column on `user` silently changes audit fingerprints across the whole table — and force PII back in (`User.email` is the obvious risk, and we just spent T6 redacting email in JWT logs); (b) diff-only entries lose context — the field name alone (`{role: "ADMIN"}`) doesn't carry resource identity. The exact field sets per action are spelled out in T5 Step 4. Storage is also a non-issue: at lolday's scale (≤ 4 admin role changes / week, ≤ 50 detector deletes / year), even a generous 1 KiB per row keeps the table under 10 MiB / decade.

**D2 — `AuditLog` insertion scope.** Exactly the three call sites the spec lists, no extensions. Insertions land in `admin.py::update_user` (role-change branch only — display-name / other patches are intentionally skipped), `datasets.py::delete_dataset`, `detectors.py::delete_detector`. Explicitly out of scope:

- `POST /jobs` (jobs/submit) — every submission is already captured in the `Job` table with full lifecycle state in `JobEvent`. An audit row would duplicate without adding security signal.
- `PATCH /users/me` (self-service preferences like display-name) — no security boundary; the only role-mutating path is `PATCH /admin/users/{id}`, which IS covered.
- `DELETE /detectors/{id}/versions/{tag}` — single-version delete is operational housekeeping (Harbor retention GC) and already touches `DetectorVersion.status=RETENTION_PRUNED|DELETED` enum (Phase 13a A4). Adding an audit row here is a follow-up, not P5 scope.
- `PATCH /credentials` / `DELETE /credentials` (Git PAT lifecycle) — high-value but spec-deferred. Track as P6 follow-up if needed.

**D3 — CSP `style-src 'self' 'unsafe-inline'`.** Kept. `'unsafe-inline'` on style-src permits inline `style=...` attributes (CSS only — `'unsafe-inline'` for style-src does NOT cover scripts; the `script-src 'self'` directive blocks any inline JS regardless). Lolday's frontend is React + Tailwind + shadcn/ui + Radix UI + recharts. Radix primitives (DropdownMenu, Popover, Dialog used throughout `src/components/ui/`) position floaters by emitting JSX `style={{position: 'absolute', top: ..., left: ...}}` which React renders as inline `style` attributes; recharts emits inline `style` on every SVG chart element. Nonce-based CSP is impractical (nginx serves static `index.html`; no per-request nonce injection without a backend rewrite) and hash-based CSP is impractical (runtime-generated style strings change per render). Industry standard for React SPAs is `style-src 'self' 'unsafe-inline'` + `script-src 'self'`. Risk is contained to CSS injection (visual defacement) — not script execution. `frontend.md` already documents the strict `script-src 'self'` discipline; T7 preserves that.

**D4 — HSTS `preload` directive shipped, registration deferred to operator.** The response header is `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`. The `preload` directive in the header is a CLAIM that the apex domain is registered at <https://hstspreload.org> — but the browser does NOT auto-add a domain to the preload list from the header alone. Registration is a separate manual submission. `lolday.connlabai.com` lives on the shared `connlabai.com` apex, and HSTS preload + `includeSubDomains` registration would lock every current and future `*.connlabai.com` subdomain to HTTPS-only for 6–12 months minimum (removal SLA). That decision belongs to whoever owns the apex DNS, not to the lolday platform. T7 ships the directive (zero operational cost — it advertises intent and is a Mozilla Observatory grading requirement) but the plan does NOT submit to hstspreload.org.

**D5 — `actions/upload-artifact` SHA pin.** Target tag `v4.6.2` (latest at planning time). The implementer captures the 40-char SHA fresh via `gh api repos/actions/upload-artifact/git/refs/tags/v4.6.2 --jq .object.sha` at T8 Step 4. Same pinning discipline as P4 (`.claude/rules/github-actions.md` §Action pinning) — Dependabot picks up future bumps. The action is referenced from `.github/workflows/images.yml`'s frontend matrix entry, not from a composite action.

**D6 — Metric naming.** Spec naming follows lolday convention without change:

| Metric                            | Suffix           | Labels       | Existing precedent                                                                   |
| --------------------------------- | ---------------- | ------------ | ------------------------------------------------------------------------------------ |
| `lolday_auth_failure_total`       | `_total` Counter | `["reason"]` | `lolday_backend_errors_total{stage}` — same `lolday_<subsystem>_<event>_total` shape |
| `lolday_rate_limit_hits_total`    | `_total` Counter | `["prefix"]` | same                                                                                 |
| `lolday_event_broker_drops_total` | `_total` Counter | `[]`         | `lolday_priority_bump_total` — same unlabeled-counter shape                          |

All three use `lolday_` prefix + `_total` suffix per Prometheus convention. Label cardinality stays bounded: `reason` ∈ {`missing_header`, `jwks_lookup_failed`, `invalid_signature`, `missing_principal_claim`} (4 values), `prefix` ∈ {`builds_create`, `jobs_create`} (2 values today — `auth` / `dataset_clone` etc. only if rate-limit grows). No `_seconds` / `_bytes` histograms in this phase.

---

## Pre-flight

- [ ] **Confirm clean working tree on `main`.**

  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  git status
  git rev-parse HEAD
  ```

  Expected: working tree clean modulo untracked `backend/kube-prometheus-stack/` (unrelated upstream chart vendor dir; tracked tech debt). HEAD at `2a92939` (P4 ship + post-merge fix-ups: `d93c6a8`, `cb59c46`, `60d911a`, `c073373`, `c44e27d`, `2a92939`) or newer.

- [ ] **Confirm helm rev ≥ 168 is the deployed release with chart v0.21.3.**

  ```bash
  helm -n lolday list | grep lolday
  ```

  Expected: `REVISION 168` (or higher), `STATUS deployed`, `CHART lolday-0.21.3`, `APP VERSION 0.21.3`.

- [ ] **Confirm Kyverno cluster policies (post-P4) are Ready.**

  ```bash
  kubectl get clusterpolicy verify-lolday-image-signatures pss-baseline-audit-lolday \
    -o jsonpath='{range .items[*]}{.metadata.name}={.status.ready}{"\n"}{end}'
  ```

  Expected:

  ```
  verify-lolday-image-signatures=true
  pss-baseline-audit-lolday=true
  ```

  Both must report `true`. P5 must not regress P4's image-signing admission gate.

- [ ] **Confirm `claims_peek` log site is importable from backend pod.**

  ```bash
  kubectl -n lolday exec deploy/backend -- /app/.venv/bin/python -c \
    "from app.auth.cf_access import resolve_user_from_jwt; print('ok:', resolve_user_from_jwt.__name__)"
  ```

  Expected: `ok: resolve_user_from_jwt`. The function holds the `claims_peek` log line that T6 redacts. P4 follow-up commits did not move it.

- [ ] **Confirm backend test baseline = 732 passed.**

  ```bash
  cd backend && uv run pytest -q 2>&1 | tail -3
  ```

  Expected: `732 passed`. P5 should land 732 + N new (N ≈ 12–18 across T1, T2, T3, T5, T6, T10, T11).

- [ ] **Confirm `helm lint` baseline.**

  Cache the canonical lint argv (P5 adds no new required values vs. P4 — Kyverno values already wired):

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test \
    --set backend.fernetKeys=test \
    --set postgresql.auth.password=test \
    --set mlflow.auth.password=test --set mlflow.db.password=test \
    --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test \
    --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`. INFO line for required `backend.harborAdminPassword` is the chart's `required()` validator surfacing, not a lint failure.

- [ ] **Confirm `pre-commit` baseline.**

  ```bash
  pre-commit run --all-files
  ```

  Expected: all hooks green. **Do NOT use `--no-verify`** at any point in P5 (project hard rule). If a hook fails, fix the root cause.

- [ ] **Create the feature branch.**

  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  git checkout -b security-hardening-p5
  ```

  The plan itself is committed directly to `main` (continuation of the security spec audit-trail pattern). All P5 task commits land on `security-hardening-p5` and squash-merge back to `main` via a single PR per the P1/P2/P3/P4 pattern.

---

## Task 1: [H-27] Authentication failure counter + Alertmanager spike rule

**Findings:** H-27 (HIGH). Recommended model: **opus** (touches `metrics.py`, four failure branches in `cf_access.py`, alertmanager rule, paired tests).

**Files:**

- Modify: `backend/app/metrics.py` (append new Counter)
- Modify: `backend/app/auth/cf_access.py:182-216`, `:222-229` (four `.labels(reason=...).inc()` calls)
- Modify: `charts/lolday/templates/monitoring/alertmanager-rules.yaml` (append `LoldayAuthFailureSpike` rule to `lolday-baseline.rules` group)
- Test: `backend/tests/test_cf_access.py` (extend existing file)

**Rationale:** Today every JWT-invalid path emits `logger.warning("cf_access 401 ...")` but no metric. An attacker probing JWTs (token stuffing, signature-strip attacks, audience-replay across CF Access apps) produces a wall of warnings in Loki but never trips Alertmanager. `lolday_auth_failure_total{reason=...}` exposes the four failure branches by attribution so the `LoldayAuthFailureSpike` rule (`rate > 0.5/s for 5m`) wakes Spidey Warnings (severity=warning per [`docs/superpowers/specs/2026-05-10-alerting-redesign-design.md`](../specs/2026-05-10-alerting-redesign-design.md) routing). Captain Hook is reserved for `severity=critical`; auth-probe spikes are warning-grade — a confirmed compromise is critical and lives elsewhere.

The four failure branches in `cf_access.resolve_user_from_jwt` (post-P3 / P4):

| Line | Branch                                                     | `reason=` label           |
| ---- | ---------------------------------------------------------- | ------------------------- |
| 182  | `if not token` — missing `Cf-Access-Jwt-Assertion` header  | `missing_header`          |
| 191  | `except pyjwt.PyJWKClientError` — JWKS lookup failure      | `jwks_lookup_failed`      |
| 202  | `except pyjwt.InvalidTokenError` — sig/aud/iss/exp invalid | `invalid_signature`       |
| 226  | `if not common_name` — neither `email` nor `common_name`   | `missing_principal_claim` |

The `verify_cf_token` audience-shape check at `cf_access.py:53-60` raises `pyjwt.InvalidAudienceError`, a subclass of `InvalidTokenError` — falls into the `invalid_signature` bucket via line 202. Do not split it out; the `reason` label cardinality stays at 4 by design.

- [ ] **Step 1: Write the failing test.**

  Append to `backend/tests/test_cf_access.py`:

  ```python
  import pytest
  from prometheus_client import REGISTRY


  def _read_counter(metric_name: str, **labels) -> float:
      """Read a labeled Counter's current value from the default REGISTRY."""
      value = REGISTRY.get_sample_value(metric_name, labels=labels)
      return 0.0 if value is None else value


  async def test_auth_failure_total_increments_on_invalid_signature(monkeypatch):
      """A JWT with a bad signature must increment AUTH_FAILURE_TOTAL{reason='invalid_signature'}."""
      from app.auth import cf_access
      from app.config import settings

      monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
      monkeypatch.setattr(settings, "CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
      monkeypatch.setattr(settings, "CF_ACCESS_APP_AUD", "test-app-uid")

      class _FakeJwksClient:
          def get_signing_key_from_jwt(self, _token):
              class _K:
                  key = b"unrelated-public-key-bytes"
              return _K()

      monkeypatch.setattr(cf_access, "_get_jwks_client", lambda: _FakeJwksClient())

      before = _read_counter("lolday_auth_failure_total", reason="invalid_signature")

      from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt

      with pytest.raises(CfAccessAuthError):
          await resolve_user_from_jwt(session=None, token="not-a-real-jwt", log_context="test")

      after = _read_counter("lolday_auth_failure_total", reason="invalid_signature")
      assert after - before == pytest.approx(1.0)


  async def test_auth_failure_total_increments_on_missing_header(monkeypatch):
      """A None token must increment AUTH_FAILURE_TOTAL{reason='missing_header'}."""
      from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
      from app.config import settings

      monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)

      before = _read_counter("lolday_auth_failure_total", reason="missing_header")

      with pytest.raises(CfAccessAuthError):
          await resolve_user_from_jwt(session=None, token=None, log_context="test")

      after = _read_counter("lolday_auth_failure_total", reason="missing_header")
      assert after - before == pytest.approx(1.0)
  ```

  Note: `@pytest.mark.asyncio` is omitted intentionally — `backend/pyproject.toml` sets `asyncio_mode = "auto"`, so every `async def test_*` is auto-collected as an asyncio test (per `.claude/rules/backend.md` §Tests).

- [ ] **Step 2: Run tests to verify they fail.**

  ```bash
  cd backend && uv run pytest tests/test_cf_access.py::test_auth_failure_total_increments_on_invalid_signature tests/test_cf_access.py::test_auth_failure_total_increments_on_missing_header -v
  ```

  Expected: both FAIL with `AttributeError: module 'app.metrics' has no attribute 'AUTH_FAILURE_TOTAL'` (or `KeyError` on the Counter labels — depends on import order).

- [ ] **Step 3: Add `AUTH_FAILURE_TOTAL` to `backend/app/metrics.py`.**

  After the existing `BACKEND_ERRORS` Counter (post line 14), append:

  ```python
  # H-27 (security-hardening P5) — Cloudflare Access JWT verification
  # failures broken out by attribution. Feeds the LoldayAuthFailureSpike
  # Alertmanager rule (rate > 0.5/s for 5m). Cardinality is bounded to
  # 4 values: missing_header, jwks_lookup_failed, invalid_signature,
  # missing_principal_claim. Do not raise label values from the request
  # (would enable cardinality blow-up via attacker-controlled errors).
  AUTH_FAILURE_TOTAL = Counter(
      "lolday_auth_failure_total",
      "Cloudflare Access JWT verifications that failed, by attribution.",
      ["reason"],
  )
  ```

- [ ] **Step 4: Wire `.labels(reason=...).inc()` in the four failure branches of `backend/app/auth/cf_access.py`.**

  Edit `resolve_user_from_jwt` (currently at lines 161–231):

  Branch 1 — `if not token` at line 182:

  ```python
      if not token:
          logger.warning(
              "cf_access 401 %s: missing Cf-Access-Jwt-Assertion",
              log_context,
          )
          AUTH_FAILURE_TOTAL.labels(reason="missing_header").inc()
          raise CfAccessAuthError("missing Cf-Access-Jwt-Assertion header")
  ```

  Branch 2 — `except pyjwt.PyJWKClientError as e` at line 191:

  ```python
      try:
          signing_key = _get_jwks_client().get_signing_key_from_jwt(token).key
      except pyjwt.PyJWKClientError as e:
          logger.warning("cf_access 401 %s: JWKS lookup failed: %s", log_context, e)
          AUTH_FAILURE_TOTAL.labels(reason="jwks_lookup_failed").inc()
          raise CfAccessAuthError(f"jwks lookup failed: {e}") from e
  ```

  Branch 3 — `except pyjwt.InvalidTokenError as e` at line 202 — the increment lands AFTER the existing `claims_peek` warning log (which T6 will redact); for T1 we only add the metric line:

  ```python
      try:
          claims = verify_cf_token(
              token=token,
              signing_key=signing_key,
              expected_aud=settings.CF_ACCESS_APP_AUD,
              expected_iss=f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
          )
      except pyjwt.InvalidTokenError as e:
          try:
              unverified = pyjwt.decode(token, options={"verify_signature": False})
              peek = {k: unverified.get(k) for k in ("aud", "iss", "email", "exp")}
          except Exception:
              peek = "unparseable"  # type: ignore[assignment]  # fallback string for error logging
          logger.warning(
              "cf_access 401 %s: JWT invalid: %s. expected_aud=%s expected_iss=%s claims_peek=%s",
              log_context,
              e,
              settings.CF_ACCESS_APP_AUD,
              f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
              peek,
          )
          AUTH_FAILURE_TOTAL.labels(reason="invalid_signature").inc()
          raise CfAccessAuthError(f"invalid Cloudflare Access token: {e}") from e
  ```

  Branch 4 — `if not common_name` at line 226:

  ```python
      email = claims.get("email")
      if not email:
          common_name = claims.get("common_name")
          if not common_name:
              logger.warning(
                  "cf_access 401 %s: JWT has neither email nor common_name claim",
                  log_context,
              )
              AUTH_FAILURE_TOTAL.labels(reason="missing_principal_claim").inc()
              raise CfAccessAuthError("token has neither email nor common_name claim")
          email = f"service-{common_name}@cf-access.local"
  ```

  Update the top-of-file import (line 23) from:

  ```python
  from app.metrics import BACKEND_ERRORS
  ```

  to:

  ```python
  from app.metrics import AUTH_FAILURE_TOTAL, BACKEND_ERRORS
  ```

- [ ] **Step 5: Run the tests to verify they pass.**

  ```bash
  cd backend && uv run pytest tests/test_cf_access.py -v
  ```

  Expected: all pre-existing `test_cf_access` tests still pass; both new tests added in Step 1 PASS.

- [ ] **Step 6: Append `LoldayAuthFailureSpike` to `charts/lolday/templates/monitoring/alertmanager-rules.yaml`.**

  In the `lolday-baseline.rules` group (after `LoldayBackendErrorRateElevated` around line 63, before `AlloyLokiWriteDroppedSamples`):

  ```yaml
  # H-27 (security-hardening P5) — JWT verification failures broken
  # out by reason. > 0.5/s sustained for 5m suggests a probe
  # (token stuffing, sig-strip, aud-replay). Warning-grade per
  # alerting redesign §6.1 — a confirmed compromise is critical
  # and lives elsewhere.
  - alert: LoldayAuthFailureSpike
    expr: sum by (reason) (rate(lolday_auth_failure_total[5m])) > 0.5
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "Authentication failure spike — reason={{`{{ $labels.reason }}`}} ({{`{{ $value | humanize }}`}}/s)"
      description: "lolday_auth_failure_total rate > 0.5/s for 5m on reason='{{`{{ $labels.reason }}`}}'. Possible JWT probe. Cross-check Loki: `kubectl -n lolday logs deploy/backend | grep 'cf_access 401'`. Confirmed probe → block at the Cloudflare Access policy level (Zero Trust → Access → Applications)."
  ```

- [ ] **Step 7: helm lint.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test \
    --set mlflow.auth.password=test --set mlflow.db.password=test \
    --set harborAdminPassword=test --set cloudflare.tunnelToken=test \
    --set grafana.adminPassword=test --set monitoring.postgresExporter.password=test
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 8: Commit.**

  ```bash
  git add backend/app/metrics.py backend/app/auth/cf_access.py \
    backend/tests/test_cf_access.py \
    charts/lolday/templates/monitoring/alertmanager-rules.yaml
  git commit -m "feat(observability): auth failure counter + alert [H-27]"
  ```

---

## Task 2: [M-ratelimit-metric] Rate-limit hits counter + Alertmanager spike rule

**Findings:** M-ratelimit-metric (MEDIUM). Recommended model: **sonnet** (single-file backend edit + alert rule append).

**Files:**

- Modify: `backend/app/metrics.py` (append new Counter)
- Modify: `backend/app/services/rate_limit.py:41-60` (increment in both `_dep` closures before the 429 raise)
- Modify: `charts/lolday/templates/monitoring/alertmanager-rules.yaml` (append `LoldayRateLimitSpike`)
- Test: `backend/tests/test_rate_limit_metric.py` (new file)

**Rationale:** The fixed-window limiter at `services/rate_limit.py` already returns 429 on overflow but emits no metric. A user repeatedly hitting `POST /jobs` past the cap, or an automated probe against `POST /detectors/{id}/builds`, is invisible to Alertmanager. The counter is incremented per-prefix so the alert can identify which call site is overflowing. Today's two prefixes are `jobs_create` (POST /jobs, 30/60s — `routers/jobs.py:141`) and `builds_create` (POST /detectors/{id}/builds, 10/3600s — `routers/detectors.py:523`); cardinality stays bounded at 2.

- [ ] **Step 1: Write the failing test.**

  Create `backend/tests/test_rate_limit_metric.py`:

  ```python
  """RATE_LIMIT_HITS_TOTAL increments when rate_limit_user / rate_limit_ip raises 429."""

  import uuid
  from unittest.mock import AsyncMock, patch

  import pytest
  from fastapi import HTTPException
  from prometheus_client import REGISTRY


  def _read(metric: str, **labels) -> float:
      v = REGISTRY.get_sample_value(metric, labels=labels)
      return 0.0 if v is None else v


  async def test_rate_limit_user_increments_metric_when_over_cap():
      from app.models import Role, User
      from app.services.rate_limit import rate_limit_user

      user = User(id=uuid.uuid4(), email="a@b", role=Role.USER, handle="h", display_name="d")
      dep = rate_limit_user("test_prefix_a", limit=1, window_seconds=60)

      before = _read("lolday_rate_limit_hits_total", prefix="test_prefix_a")

      with patch("app.services.rate_limit.check_rate", new=AsyncMock(return_value=False)):
          with pytest.raises(HTTPException) as ei:
              await dep(user=user)
          assert ei.value.status_code == 429

      after = _read("lolday_rate_limit_hits_total", prefix="test_prefix_a")
      assert after - before == pytest.approx(1.0)


  async def test_rate_limit_user_does_not_increment_when_under_cap():
      from app.models import Role, User
      from app.services.rate_limit import rate_limit_user

      user = User(id=uuid.uuid4(), email="a@b", role=Role.USER, handle="h", display_name="d")
      dep = rate_limit_user("test_prefix_b", limit=10, window_seconds=60)

      before = _read("lolday_rate_limit_hits_total", prefix="test_prefix_b")

      with patch("app.services.rate_limit.check_rate", new=AsyncMock(return_value=True)):
          await dep(user=user)

      after = _read("lolday_rate_limit_hits_total", prefix="test_prefix_b")
      assert after == before
  ```

- [ ] **Step 2: Run test to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_rate_limit_metric.py -v
  ```

  Expected: FAIL — `AttributeError` on `RATE_LIMIT_HITS_TOTAL`.

- [ ] **Step 3: Add `RATE_LIMIT_HITS_TOTAL` to `backend/app/metrics.py`.**

  Append after `AUTH_FAILURE_TOTAL` (added in T1):

  ```python
  # M-ratelimit-metric (security-hardening P5) — fixed-window limiter
  # overflows (HTTP 429) attributed by prefix. Two prefixes today:
  # jobs_create (POST /jobs) and builds_create (POST /detectors/{id}/builds).
  # Feeds the LoldayRateLimitSpike rule (rate > 1/s for 10m).
  RATE_LIMIT_HITS_TOTAL = Counter(
      "lolday_rate_limit_hits_total",
      "Rate-limit 429 responses, by prefix label.",
      ["prefix"],
  )
  ```

- [ ] **Step 4: Wire the increment in `backend/app/services/rate_limit.py`.**

  Replace the body of `rate_limit_user` + `rate_limit_ip` (lines 41–60):

  ```python
  from app.metrics import RATE_LIMIT_HITS_TOTAL


  def rate_limit_user(prefix: str, limit: int, window_seconds: int):
      async def _dep(user: User = Depends(current_active_user)) -> None:
          if not await check_rate(f"rl:{prefix}:{user.id}", limit, window_seconds):
              RATE_LIMIT_HITS_TOTAL.labels(prefix=prefix).inc()
              raise HTTPException(status_code=429, detail="rate limited")

      return _dep


  def rate_limit_ip(prefix: str, limit: int, window_seconds: int):
      async def _dep(request: Request) -> None:
          if request.client is None:
              raise HTTPException(status_code=400, detail="client address required")
          ip = request.client.host
          if not await check_rate(f"rl:{prefix}:{ip}", limit, window_seconds):
              RATE_LIMIT_HITS_TOTAL.labels(prefix=prefix).inc()
              raise HTTPException(status_code=429, detail="rate limited")

      return _dep
  ```

  (Preserve the existing `# misconfigured proxy or malformed request` comment in `rate_limit_ip` — only the trailing increment line changes.)

  Update the top-of-file import block to add `RATE_LIMIT_HITS_TOTAL`. Inline imports inside the closures are equally valid; module-top import is cleaner.

- [ ] **Step 5: Run test to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_rate_limit_metric.py -v
  ```

  Expected: both tests PASS.

- [ ] **Step 6: Append `LoldayRateLimitSpike` to `alertmanager-rules.yaml`.**

  In the `lolday-baseline.rules` group, after `LoldayAuthFailureSpike` (added in T1):

  ```yaml
  # M-ratelimit-metric (security-hardening P5) — overflow attributed
  # by prefix. 1/s over 10m suggests either a misbehaving client or
  # a legitimate user hitting the cap; check the prefix label first.
  # builds_create is 10/hour — easy to trip in batch use; jobs_create
  # is 30/min — much harder to trip benignly.
  - alert: LoldayRateLimitSpike
    expr: sum by (prefix) (rate(lolday_rate_limit_hits_total[10m])) > 1
    for: 10m
    labels:
      severity: warning
    annotations:
      summary: "Rate-limit overflow — prefix={{`{{ $labels.prefix }}`}} ({{`{{ $value | humanize }}`}}/s)"
      description: "lolday_rate_limit_hits_total rate > 1/s for 10m on prefix='{{`{{ $labels.prefix }}`}}'. Inspect Redis bucket keys: `kubectl -n lolday exec deploy/redis -- redis-cli --scan --pattern 'rl:{{`{{ $labels.prefix }}`}}:*' | head`. If a legitimate user is hammering the cap, contact them; if a probe, block at the Cloudflare Access policy level."
  ```

- [ ] **Step 7: helm lint + commit.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test

  git add backend/app/metrics.py backend/app/services/rate_limit.py \
    backend/tests/test_rate_limit_metric.py \
    charts/lolday/templates/monitoring/alertmanager-rules.yaml
  git commit -m "feat(observability): rate-limit hits counter + alert [M-ratelimit-metric]"
  ```

---

## Task 3: [L-event-broker-drops] Event broker drop counter

**Findings:** L-event-broker-drops (LOW). Recommended model: **sonnet** (single line increment + Counter + test).

**Files:**

- Modify: `backend/app/metrics.py` (append unlabeled Counter)
- Modify: `backend/app/services/events_tail.py:60-70` (increment in drop-oldest branch of `EventBroker.publish`)
- Test: `backend/tests/services/test_event_broker_metric.py` (new file)

**Rationale:** `EventBroker.publish` (the WebSocket fan-out used by `useJobEvents.ts`) silently drops the oldest queue entry when a subscriber's `asyncio.Queue(maxsize=1000)` is full — a `_log.warning("event_broker_dropped_oldest", ...)` line is emitted but produces no metric. If a runaway producer floods the broker (e.g., a single noisy detector emits 10k events/sec), the warnings flood Loki but Alertmanager stays silent. No new alert rule in this phase — the spec calls for the counter only; rule sits as P6 follow-up if the counter ever lights up in real traffic. Counter is unlabeled (label cardinality on `job_id` would explode — each Volcano job ID is unique).

- [ ] **Step 1: Write the failing test.**

  Create `backend/tests/services/test_event_broker_metric.py`:

  ```python
  """EVENT_BROKER_DROPS_TOTAL must increment exactly once per drop."""

  import uuid

  import pytest
  from prometheus_client import REGISTRY


  def _read(metric: str) -> float:
      v = REGISTRY.get_sample_value(metric)
      return 0.0 if v is None else v


  async def test_event_broker_drops_total_increments_on_overflow():
      from app.services.events_tail import EventBroker

      broker = EventBroker()
      job_id = uuid.uuid4()
      q = broker.subscribe(job_id)

      # Saturate the queue (maxsize=1000); the 1001st publish triggers drop-oldest.
      for i in range(1000):
          q.put_nowait({"i": i, "kind": "fill"})

      before = _read("lolday_event_broker_drops_total")
      await broker.publish(job_id, {"kind": "overflow", "id": "boom"})
      after = _read("lolday_event_broker_drops_total")

      assert after - before == pytest.approx(1.0)
      # And the overflow event reached the subscriber after one drop.
      drained = []
      while not q.empty():
          drained.append(q.get_nowait())
      assert drained[-1]["kind"] == "overflow"
  ```

- [ ] **Step 2: Run test to verify failure.**

  ```bash
  cd backend && uv run pytest tests/services/test_event_broker_metric.py -v
  ```

  Expected: FAIL — `AttributeError` on `EVENT_BROKER_DROPS_TOTAL`.

- [ ] **Step 3: Add `EVENT_BROKER_DROPS_TOTAL` to `backend/app/metrics.py`.**

  Append after `RATE_LIMIT_HITS_TOTAL`:

  ```python
  # L-event-broker-drops (security-hardening P5) — EventBroker.publish
  # discards the oldest queue entry when a subscriber's bounded Queue
  # (maxsize=1000 in events_tail) is full. Unlabeled — job_id labels
  # would blow up cardinality.
  EVENT_BROKER_DROPS_TOTAL = Counter(
      "lolday_event_broker_drops_total",
      "EventBroker.publish drop-oldest events (subscriber queue saturated).",
  )
  ```

- [ ] **Step 4: Wire the increment in `backend/app/services/events_tail.py`.**

  Replace the body of `EventBroker.publish` (lines 59–70). Add the import line near the top of the file (after `from app.models import JobEvent`):

  ```python
  from app.metrics import EVENT_BROKER_DROPS_TOTAL


  # ...inside class EventBroker:

      async def publish(self, job_id: uuid.UUID, event: dict[str, Any]) -> None:
          for q in list(self._subscribers.get(job_id, [])):
              try:
                  q.put_nowait(event)
              except asyncio.QueueFull:
                  with contextlib.suppress(asyncio.QueueEmpty):
                      q.get_nowait()
                  q.put_nowait(event)
                  EVENT_BROKER_DROPS_TOTAL.inc()
                  _log.warning(
                      "event_broker_dropped_oldest",
                      extra={"job_id": str(job_id), "kind": event.get("kind")},
                  )
  ```

- [ ] **Step 5: Run test to verify pass.**

  ```bash
  cd backend && uv run pytest tests/services/test_event_broker_metric.py -v
  ```

  Expected: PASS.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/app/metrics.py backend/app/services/events_tail.py \
    backend/tests/services/test_event_broker_metric.py
  git commit -m "feat(observability): event-broker drop counter [L-event-broker-drops]"
  ```

---

## Task 4: [L-discord-alert] Alertmanager rule for Discord notify failures

**Findings:** L-discord-alert (LOW). Recommended model: **sonnet** (rule-only; no Python edit).

**Files:**

- Modify: `charts/lolday/templates/monitoring/alertmanager-rules.yaml` (append `LoldayDiscordNotifyFailing`)

**Rationale:** `services/notify.py` already increments `BACKEND_ERRORS{stage="discord_notify"}` on every webhook failure (5s timeout, swallowed exception, per `.claude/rules/backend.md` §Discord notify pattern). The existing `LoldayBackendErrorRateElevated` rule (line ~55 of `alertmanager-rules.yaml`) fires on ANY non-zero stage — but the Discord-specific failure mode is special: it means user-targeted notifications are dropping silently, which is a UX regression rather than a backend bug. A dedicated rule with a higher threshold (`> 0.1/s for 10m`) avoids paging on a single transient 5xx from Discord, while a sustained outage paints a clearer incident annotation. No new counter — the existing `BACKEND_ERRORS{stage="discord_notify"}` is the canonical signal.

- [ ] **Step 1: Append the rule.**

  In `charts/lolday/templates/monitoring/alertmanager-rules.yaml`, in the `lolday-baseline.rules` group, after `LoldayRateLimitSpike` (added in T2):

  ```yaml
  # L-discord-alert (security-hardening P5) — sustained Discord
  # notify drops. notify.py swallows the exception and bumps
  # BACKEND_ERRORS{stage="discord_notify"}; LoldayBackendErrorRateElevated
  # would fire on a single transient 5xx, which is noise for
  # Discord. This rule waits for a sustained pattern (> 0.1/s
  # over 10m ≈ ≥60 failures in 10m).
  - alert: LoldayDiscordNotifyFailing
    expr: rate(lolday_backend_errors_total{stage="discord_notify"}[10m]) > 0.1
    for: 10m
    labels:
      severity: warning
    annotations:
      summary: "Discord notify dropping ({{`{{ $value | humanize }}`}}/s for 10m)"
      description: "Discord notify failures sustained > 0.1/s for 10m. User-targeted Spidey Service Alerts may be missing. Check Discord webhook status (rotate via /credentials if compromised) and notify.py log lines (`kubectl -n lolday logs deploy/backend | grep discord_notify`). Webhook env var: DISCORD_WEBHOOK_URL_EVENTS in `.lolday-secrets.env`."
  ```

- [ ] **Step 2: helm lint + commit.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test

  git add charts/lolday/templates/monitoring/alertmanager-rules.yaml
  git commit -m "feat(observability): discord notify failing alert [L-discord-alert]"
  ```

---

## Task 5: [M-audit-log] AuditLog model + migration + 3 router insertions

**Findings:** M-audit-log (MEDIUM). Recommended model: **opus** (new SQLAlchemy model + Alembic migration + small `services/audit.py` writer + three router edits + paired tests).

**Files:**

- Create: `backend/app/models/audit.py` (new SQLAlchemy model `AuditLog`)
- Modify: `backend/app/models/__init__.py` (re-export `AuditLog`)
- Create: `backend/migrations/versions/<REV>_add_audit_log_table.py` (alembic-generated; name kept verbatim per `.claude/rules/alembic-migrations.md`)
- Create: `backend/app/services/audit.py` (small `write_audit_log()` helper)
- Modify: `backend/app/routers/admin.py:77-91` (insert audit row inside role-change branch)
- Modify: `backend/app/routers/datasets.py:245-275` (insert audit row before commit)
- Modify: `backend/app/routers/detectors.py:306-342` (insert audit row before soft-delete commit)
- Test: `backend/tests/test_audit_log.py` (new file)
- Test: `backend/tests/test_migrations_audit_log.py` (new file, simple upgrade/downgrade round-trip on aiosqlite)

**Rationale:** Today, an admin demotes another admin and the only forensic trail is a `logger.info("admin role change: ...")` line in Loki, which has a 30-day retention. There is no queryable record from inside the platform. Dataset and detector deletes are soft-deletes (`deleted_at` set on the row), so the actor identity is implied by ownership rules but never explicitly captured. `AuditLog` is the queryable, append-only trail. Per D1, payloads are small cherry-picked dicts. Per D2, exactly three call sites — admin role-change, dataset delete, detector delete — match spec scope.

The cherry-picked field sets per action:

| `action`            | `target_type` | `target_id` | `before_jsonb`                                        | `after_jsonb`                   |
| ------------------- | ------------- | ----------- | ----------------------------------------------------- | ------------------------------- |
| `admin.role_change` | `user`        | target user | `{"role": "<old enum value>"}`                        | `{"role": "<new enum value>"}`  |
| `dataset.delete`    | `dataset`     | dataset     | `{"name": ..., "visibility": ...}`                    | `{"deleted_at": "<isoformat>"}` |
| `detector.delete`   | `detector`    | detector    | `{"name": ..., "git_url": ..., "owner_id": "<uuid>"}` | `{"deleted_at": "<isoformat>"}` |

The reference `services/audit.py::write_audit_log(session, actor_id, action, target_type, target_id, before, after)` is a thin wrapper — it constructs the `AuditLog` row, `session.add()`s it, and lets the caller's outer `await session.commit()` flush both the resource mutation and the audit row in a single transaction. The audit row is best-effort within the transaction — if the outer commit fails, both the mutation and the audit row roll back together. There is intentionally NO try/except around `write_audit_log` in the routers; that would re-open the silent-failure surface this finding was created to close.

- [ ] **Step 1: Write the failing tests.**

  Create `backend/tests/test_audit_log.py`:

  ```python
  """AuditLog rows must be written on admin role change, dataset delete, detector delete."""

  import uuid

  from sqlalchemy import select


  async def test_audit_log_written_on_admin_role_change(auth_client_admin, db_session):
      """PATCH /admin/users/{id} with role-change body writes one audit_log row."""
      from app.models import AuditLog, Role, User

      target = User(
          id=uuid.uuid4(), email="bob@example.com",
          role=Role.USER, handle="bob", display_name="Bob",
      )
      db_session.add(target)
      await db_session.commit()

      resp = await auth_client_admin.patch(
          f"/api/v1/admin/users/{target.id}",
          json={"role": "developer"},
      )
      assert resp.status_code == 200

      rows = (await db_session.execute(
          select(AuditLog).where(
              AuditLog.target_id == target.id,
              AuditLog.action == "admin.role_change",
          )
      )).scalars().all()
      assert len(rows) == 1
      assert rows[0].before_jsonb == {"role": "user"}
      assert rows[0].after_jsonb == {"role": "developer"}
      assert rows[0].target_type == "user"


  async def test_audit_log_written_on_dataset_delete(user_client, db_session):
      """DELETE /datasets/{id} writes one audit_log row."""
      from app.models import AuditLog

      # Seed a dataset owned by user_client's identity (user1@example.dev per conftest).
      # Pattern: lift from an existing dataset test — grep `delete_dataset` in
      # backend/tests/test_datasets*.py for the canonical seeding flow (POST /datasets
      # with a tiny CSV body, or direct DB seed via db_session.add + commit).
      ds_id = await _seed_dataset_for_user_client(db_session, user_client)

      resp = await user_client.delete(f"/api/v1/datasets/{ds_id}")
      assert resp.status_code == 204

      rows = (await db_session.execute(
          select(AuditLog).where(
              AuditLog.target_id == ds_id,
              AuditLog.action == "dataset.delete",
          )
      )).scalars().all()
      assert len(rows) == 1
      assert rows[0].target_type == "dataset"
      assert "name" in rows[0].before_jsonb
      assert "deleted_at" in rows[0].after_jsonb


  async def test_audit_log_written_on_detector_delete(user_client, db_session):
      """DELETE /detectors/{id} writes one audit_log row."""
      from app.models import AuditLog

      # Seed a detector owned by user_client. Pattern: mirror `test_detectors_*.py`
      # — the standard seed inserts a Detector row directly via db_session
      # (skipping the git-clone register flow) so the test stays fast.
      det_id = await _seed_detector_for_user_client(db_session, user_client)

      resp = await user_client.delete(f"/api/v1/detectors/{det_id}")
      assert resp.status_code == 204

      rows = (await db_session.execute(
          select(AuditLog).where(
              AuditLog.target_id == det_id,
              AuditLog.action == "detector.delete",
          )
      )).scalars().all()
      assert len(rows) == 1
      assert rows[0].target_type == "detector"
      assert "git_url" in rows[0].before_jsonb
      assert "deleted_at" in rows[0].after_jsonb


  async def test_audit_log_not_written_on_no_op_patch(auth_client_admin, db_session):
      """PATCH /admin/users/{id} with empty body must NOT write an audit row."""
      from app.models import AuditLog, Role, User

      target = User(
          id=uuid.uuid4(), email="carol@example.com",
          role=Role.USER, handle="carol", display_name="Carol",
      )
      db_session.add(target)
      await db_session.commit()

      resp = await auth_client_admin.patch(
          f"/api/v1/admin/users/{target.id}",
          json={},
      )
      assert resp.status_code == 200

      rows = (await db_session.execute(
          select(AuditLog).where(AuditLog.target_id == target.id)
      )).scalars().all()
      assert rows == []
  ```

  Fixture provenance: `auth_client_admin`, `user_client`, `db_session` all already exist in `backend/tests/conftest.py` (lines 176, 191, 215 respectively as of `2a92939`). No conftest extension required. The `_seed_dataset_for_user_client` / `_seed_detector_for_user_client` helpers are test-file-local: lift the seed pattern from existing `backend/tests/test_datasets*.py` / `test_detectors*.py` (typically: build a model instance, `db_session.add(it)`, `await db_session.commit()`, return `it.id`). Do NOT add fixtures to `conftest.py` for these one-shot helpers — keep them local to the test module.

  Create `backend/tests/test_migrations_audit_log.py`:

  ```python
  """Verify the new audit_log migration upgrades and downgrades cleanly on aiosqlite."""

  import pytest
  from alembic import command
  from alembic.config import Config


  @pytest.mark.no_mock_mlflow
  def test_audit_log_upgrade_downgrade_round_trip(tmp_path):
      """Upgrade head, downgrade one step, upgrade head — schema must reach head both times."""
      db_file = tmp_path / "audit_round_trip.sqlite"
      cfg = Config("alembic.ini")
      cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")

      command.upgrade(cfg, "head")
      command.downgrade(cfg, "-1")
      command.upgrade(cfg, "head")

      # The model is importable + table present after the round trip.
      from sqlalchemy import create_engine, inspect
      engine = create_engine(f"sqlite:///{db_file}")
      assert "audit_log" in inspect(engine).get_table_names()
  ```

- [ ] **Step 2: Run tests to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_audit_log.py tests/test_migrations_audit_log.py -v
  ```

  Expected: all FAIL — `ImportError` on `AuditLog`.

- [ ] **Step 3: Create the SQLAlchemy model `backend/app/models/audit.py`.**

  ```python
  """Audit log — append-only record of security-relevant actions.

  Per spec 2026-05-12-security-hardening-design.md §6.5 (M-audit-log) and
  plan 2026-05-14-security-hardening-p5-audit-observability.md design
  decision D1, payloads in before_jsonb / after_jsonb are intentionally
  cherry-picked per call-site rather than full ORM row dumps:
  schema-coupling avoidance + PII control + bounded storage.

  The table is append-only: there is no UPDATE or DELETE path in the
  codebase. Operators who need to redact a row (e.g. GDPR right-to-be-
  forgotten) do so out-of-band via psql.
  """

  import uuid
  from datetime import datetime
  from typing import Any

  from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, func
  from sqlalchemy.dialects.postgresql import JSONB
  from sqlalchemy.dialects.postgresql import UUID as PG_UUID
  from sqlalchemy.orm import Mapped, mapped_column, relationship

  from app.models.user import Base, User

  _JSONB = JSON().with_variant(JSONB(), "postgresql")


  class AuditLog(Base):
      __tablename__ = "audit_log"

      id: Mapped[uuid.UUID] = mapped_column(
          PG_UUID(as_uuid=True).with_variant(
              # aiosqlite uses CHAR(36); same pattern as other UUID columns in this codebase.
              String(36),
              "sqlite",
          ),
          primary_key=True,
          default=uuid.uuid4,
      )
      actor_id: Mapped[uuid.UUID] = mapped_column(
          PG_UUID(as_uuid=True).with_variant(String(36), "sqlite"),
          ForeignKey("user.id", ondelete="RESTRICT"),
          nullable=False,
      )
      action: Mapped[str] = mapped_column(String(64), nullable=False)
      target_type: Mapped[str] = mapped_column(String(32), nullable=False)
      target_id: Mapped[uuid.UUID] = mapped_column(
          PG_UUID(as_uuid=True).with_variant(String(36), "sqlite"),
          nullable=False,
      )
      before_jsonb: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
      after_jsonb: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
      ts: Mapped[datetime] = mapped_column(
          DateTime(timezone=True), server_default=func.now(), nullable=False
      )

      actor: Mapped[User] = relationship(foreign_keys=[actor_id])

      __table_args__ = (
          Index("ix_audit_log_target_ts", "target_type", "target_id", "ts"),
          Index("ix_audit_log_actor_ts", "actor_id", "ts"),
      )
  ```

- [ ] **Step 4: Re-export from `backend/app/models/__init__.py`.**

  Add the import + `__all__` entry alongside existing models:

  ```python
  from app.models.audit import AuditLog
  # ... existing imports

  __all__ = [
      # ... existing
      "AuditLog",
      # ... existing
  ]
  ```

  Place `"AuditLog",` in alphabetical position (between `"Base",` and `"DatasetConfig",`).

- [ ] **Step 5: Generate the Alembic migration.**

  ```bash
  cd backend && uv run alembic revision --autogenerate -m "add audit_log table"
  ```

  The generated file lives at `backend/migrations/versions/<REV>_add_audit_log_table.py`. Keep alembic's auto-generated filename verbatim per `.claude/rules/alembic-migrations.md` §Filename convention.

  **Manually review the autogenerated migration** — autogenerate is unreliable; verify:
  - `op.create_table("audit_log", ...)` includes all 7 columns + correct types
  - JSONB columns use the `with_variant` pattern (autogenerate sometimes drops this)
  - Both indexes (`ix_audit_log_target_ts`, `ix_audit_log_actor_ts`) are created
  - `downgrade()` drops the indexes + table in reverse order

  Spec-coupled comment at the top of the migration file:

  ```python
  """add audit_log table

  Revision ID: <REV>
  Revises: 1afdf61e18f9
  Create Date: 2026-05-14 ...

  Spec: docs/superpowers/specs/2026-05-12-security-hardening-design.md §6.5
  Finding: M-audit-log

  Append-only audit trail for admin role-change, dataset.delete,
  detector.delete (3 spec-listed sites, per plan design decision D2).
  before_jsonb / after_jsonb are cherry-picked per call-site (D1) —
  small dicts, not full ORM row dumps.
  """
  ```

- [ ] **Step 6: Create `backend/app/services/audit.py`.**

  ```python
  """Audit log writer — thin wrapper that defers commit to the caller.

  Caller pattern: the router has already mutated a resource and is about
  to `await session.commit()`. write_audit_log() appends an AuditLog row
  to the same session so the commit flushes both in one transaction.
  If the commit fails, both roll back together. There is intentionally
  NO try/except inside this function — silent-failure on the audit path
  is exactly the bug this module exists to close.
  """

  from typing import Any
  from uuid import UUID

  from sqlalchemy.ext.asyncio import AsyncSession

  from app.models import AuditLog


  async def write_audit_log(
      session: AsyncSession,
      *,
      actor_id: UUID,
      action: str,
      target_type: str,
      target_id: UUID,
      before: dict[str, Any] | None = None,
      after: dict[str, Any] | None = None,
  ) -> None:
      """Append an audit row. Caller commits in its own transaction."""
      row = AuditLog(
          actor_id=actor_id,
          action=action,
          target_type=target_type,
          target_id=target_id,
          before_jsonb=before,
          after_jsonb=after,
      )
      session.add(row)
  ```

- [ ] **Step 7: Wire `write_audit_log` into `backend/app/routers/admin.py:77-91`.**

  Replace the role-change branch in `update_user`:

  ```python
  from app.services.audit import write_audit_log


  # ...inside update_user, after the last-admin safeguard, replacing lines 77-91:

      old_role = target.role
      for field, value in changes.items():
          setattr(target, field, value)
      session.add(target)

      if new_role is not None and new_role != old_role:
          await write_audit_log(
              session,
              actor_id=admin.id,
              action="admin.role_change",
              target_type="user",
              target_id=target.id,
              before={"role": old_role.value},
              after={"role": target.role.value},
          )
          logger.info(
              "admin role change: actor=%s target=%s old=%s new=%s",
              admin.email,
              target.email,
              old_role.value,
              target.role.value,
          )

      await session.commit()
      await session.refresh(target)
      return target
  ```

  (Note the reordering: audit row is staged BEFORE `session.commit()`, so the commit flushes both. The existing `await session.commit()` at line 81 moves down to after the audit-staging block. The existing `logger.info` line stays — Loki retains the human-readable line for ad-hoc grep alongside the structured audit row.)

- [ ] **Step 8: Wire `write_audit_log` into `backend/app/routers/datasets.py:245-275`.**

  Replace the body of `delete_dataset`:

  ```python
  from app.services.audit import write_audit_log


  # ...inside delete_dataset, replacing lines 272-273:

      ds.deleted_at = datetime.now(UTC)
      await write_audit_log(
          session,
          actor_id=user.id,
          action="dataset.delete",
          target_type="dataset",
          target_id=ds.id,
          before={"name": ds.name, "visibility": ds.visibility.value},
          after={"deleted_at": ds.deleted_at.isoformat()},
      )
      await session.commit()
      return Response(status_code=204)
  ```

- [ ] **Step 9: Wire `write_audit_log` into `backend/app/routers/detectors.py:306-342`.**

  Replace the body of `delete_detector` between `detector.deleted_at = ...` and `await session.commit()`:

  ```python
  from app.services.audit import write_audit_log


  # ...inside delete_detector, replacing lines 329-332:

      detector_name = detector.name
      detector_id = detector.id
      # Capture the soft-delete pre-image before the mutation.
      audit_before = {
          "name": detector.name,
          "git_url": detector.git_url,
          "owner_id": str(detector.owner_id),
      }
      detector.deleted_at = datetime.now(UTC)
      await write_audit_log(
          session,
          actor_id=detector.owner_id,  # require_detector_access(write=True) gates this
          action="detector.delete",
          target_type="detector",
          target_id=detector_id,
          before=audit_before,
          after={"deleted_at": detector.deleted_at.isoformat()},
      )
      await session.commit()
      # Best-effort Harbor cleanup ...
  ```

  Note: `actor_id` should ideally be the request user, not the detector owner — but `delete_detector` does not currently take `user: User = Depends(current_active_user)` (it depends on `require_detector_access(write=True)` which returns the detector, not the user). For this task, add a `user: User = Depends(current_active_user)` parameter to the function signature and use `user.id` instead. The existing `require_detector_access(write=True)` already enforces that the caller has write permission, so adding the user parameter is purely for audit attribution — no authorization change. Adjust the snippet above to use `actor_id=user.id`.

- [ ] **Step 10: Run all tests to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_audit_log.py tests/test_migrations_audit_log.py -v
  ```

  Expected: all PASS.

- [ ] **Step 11: Run full test suite to verify no regression.**

  ```bash
  cd backend && uv run pytest -q 2>&1 | tail -5
  ```

  Expected: previous 732 baseline + new tests, all passing. If a pre-existing admin / dataset / detector test fails because it now expects an audit row, add the audit assertion to that test (the audit row is a guaranteed side-effect of the corresponding mutation, by design).

- [ ] **Step 12: Commit.**

  ```bash
  git add backend/app/models/audit.py backend/app/models/__init__.py \
    backend/migrations/versions/*_add_audit_log_table.py \
    backend/app/services/audit.py \
    backend/app/routers/admin.py backend/app/routers/datasets.py backend/app/routers/detectors.py \
    backend/tests/test_audit_log.py backend/tests/test_migrations_audit_log.py
  git commit -m "feat(audit): AuditLog model + 3 insertion sites [M-audit-log]"
  ```

---

## Task 6: [M-jwt-email-pii] Redact email in JWT-invalid log

**Findings:** M-jwt-email-pii (MEDIUM). Recommended model: **sonnet** (~10 lines + tests).

**Files:**

- Modify: `backend/app/auth/cf_access.py:200-216` (add `redact_email()` helper + apply to `claims_peek`)
- Test: `backend/tests/test_cf_access.py` (extend with redaction tests)

**Rationale:** When a JWT verification fails, the warning log line prints `claims_peek={..., 'email': '<raw email>'}`. Loki retention is 30 days with no field-level redaction; an attacker who can read Loki (operator + future intern accounts) sees every email address that ever tried to authenticate. The redaction follows the mainstream pattern (`a***@example.com`) so the local-part length is hidden but the domain is preserved for legitimate operator triage ("is the bad-token spam coming from corporate accounts or from random external addresses?").

The redaction is one-way and not cryptographic — its goal is reducing logged-PII volume, not protecting against a determined operator who could still cross-reference the `aud` / `iss` claims. Mainstream pattern; do not over-engineer with hashing or HMAC.

- [ ] **Step 1: Write the failing test.**

  Append to `backend/tests/test_cf_access.py`:

  ```python
  import pytest


  @pytest.mark.parametrize(
      "raw,expected",
      [
          ("alice@example.com", "a***@example.com"),
          ("b@example.com", "b***@example.com"),
          ("verylonglocalpart@subdomain.example.org", "v***@subdomain.example.org"),
          # malformed inputs degrade safely, never raise
          ("no-at-sign", "<redacted-malformed>"),
          ("", "<redacted-malformed>"),
          (None, "<redacted-none>"),
      ],
  )
  def test_redact_email(raw, expected):
      from app.auth.cf_access import redact_email
      assert redact_email(raw) == expected


  async def test_claims_peek_redacts_email(monkeypatch):
      """The claims_peek warning log line must not contain a raw email after T6 lands.

      Direct logger-capture pattern (NOT pytest's caplog) — avoids the
      alembic ``disable_existing_loggers`` interaction documented in
      auto-memory ``project_caplog_alembic_logger_disabled.md``.
      """
      import io
      import logging

      from app.auth import cf_access
      from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
      from app.config import settings

      monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
      monkeypatch.setattr(settings, "CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
      monkeypatch.setattr(settings, "CF_ACCESS_APP_AUD", "test-app-uid")

      class _FakeJwksClient:
          def get_signing_key_from_jwt(self, _token):
              class _K:
                  key = b"unrelated"
              return _K()

      monkeypatch.setattr(cf_access, "_get_jwks_client", lambda: _FakeJwksClient())

      logger = logging.getLogger("app.auth.cf_access")
      saved_disabled = logger.disabled
      logger.disabled = False
      buf = io.StringIO()
      handler = logging.StreamHandler(buf)
      handler.setLevel(logging.WARNING)
      logger.addHandler(handler)
      try:
          with pytest.raises(CfAccessAuthError):
              await resolve_user_from_jwt(session=None, token="not-a-real-jwt", log_context="test")
      finally:
          logger.removeHandler(handler)
          logger.disabled = saved_disabled

      log_text = buf.getvalue()
      assert "alice@example.com" not in log_text
      # The bad token can't be decoded, so the peek dict becomes "unparseable".
      # The redaction itself is independently verified by test_redact_email above;
      # this test pins down that the WARNING line never carries a raw email.
  ```

  The integration test above only confirms the negative — no raw email leaks through. The positive ("a\*\*\*@example.com" actually appears in the log line for a parseable token") is covered indirectly by `test_redact_email` + code review of the `peek = {...}` construction. Combined coverage is sufficient.

- [ ] **Step 2: Run tests to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_cf_access.py::test_redact_email tests/test_cf_access.py::test_claims_peek_redacts_email -v
  ```

  Expected: FAIL — `ImportError` on `redact_email`.

- [ ] **Step 3: Add `redact_email()` to `backend/app/auth/cf_access.py`.**

  Insert near the top of the file, after the `_REQUIRED_CLAIMS` constant (currently line 30):

  ```python
  def redact_email(value: str | None) -> str:
      """Return a logging-safe form of an email address.

      ``alice@example.com`` -> ``a***@example.com``. The local part length
      is hidden so an attacker reading Loki can't fingerprint by local-part
      character count; the domain is preserved so operators can still
      distinguish corporate-vs-external traffic during incident triage.

      Malformed inputs (no '@', empty, None) degrade to a fixed sentinel
      string so the redacted form is never the raw input.
      """
      if value is None:
          return "<redacted-none>"
      if not value or "@" not in value:
          return "<redacted-malformed>"
      first, _, domain = value.partition("@")
      if not first:
          return "<redacted-malformed>"
      return f"{first[0]}***@{domain}"
  ```

- [ ] **Step 4: Apply `redact_email` to the `claims_peek` site at `cf_access.py:200-216`.**

  Replace the existing `except pyjwt.InvalidTokenError` block (with T1's metric increment already in place):

  ```python
      try:
          claims = verify_cf_token(
              token=token,
              signing_key=signing_key,
              expected_aud=settings.CF_ACCESS_APP_AUD,
              expected_iss=f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
          )
      except pyjwt.InvalidTokenError as e:
          try:
              unverified = pyjwt.decode(token, options={"verify_signature": False})
              peek = {
                  "aud": unverified.get("aud"),
                  "iss": unverified.get("iss"),
                  "email": redact_email(unverified.get("email")),
                  "exp": unverified.get("exp"),
              }
          except Exception:
              peek = "unparseable"  # type: ignore[assignment]  # fallback string for error logging
          logger.warning(
              "cf_access 401 %s: JWT invalid: %s. expected_aud=%s expected_iss=%s claims_peek=%s",
              log_context,
              e,
              settings.CF_ACCESS_APP_AUD,
              f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
              peek,
          )
          AUTH_FAILURE_TOTAL.labels(reason="invalid_signature").inc()
          raise CfAccessAuthError(f"invalid Cloudflare Access token: {e}") from e
  ```

- [ ] **Step 5: Run tests to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_cf_access.py -v
  ```

  Expected: all PASS.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/app/auth/cf_access.py backend/tests/test_cf_access.py
  git commit -m "feat(auth): redact email in JWT-invalid log [M-jwt-email-pii]"
  ```

---

## Task 7: [F-csp-headers] Extend CSP + add Permissions-Policy / COOP / CORP / HSTS

**Findings:** F-csp-headers (MEDIUM). Recommended model: **opus** (touches the production nginx surface; CSP rewrites tend to surface client-side breakage).

**Files:**

- Modify: `frontend/nginx.conf` (replace single CSP `add_header` with a full hardening header block)

**Rationale:** Today the prod nginx ships three baseline headers (`X-Content-Type-Options nosniff`, `X-Frame-Options DENY`, `Referrer-Policy strict-origin-when-cross-origin`) and a single thin CSP (`default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'`). Missing: `connect-src`, `font-src`, `form-action`, `frame-ancestors`, `base-uri`, `object-src`, `upgrade-insecure-requests`, `Permissions-Policy`, COOP / CORP, and HSTS. The full set is the Mozilla Observatory A+ baseline for a React SPA behind Cloudflare Access.

Design notes baked in here (cross-reference plan §Design decisions):

- **D3**: `style-src 'self' 'unsafe-inline'` retained — Radix UI + recharts emit inline `style` attributes via React's `style={{...}}` prop. `'unsafe-inline'` for style-src does NOT affect script execution (which is constrained to `script-src 'self'`); the residual risk is CSS injection only.
- **D4**: `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload` ships the header. Operator separately decides whether to register at <https://hstspreload.org> for browser preloading (apex domain shared with non-lolday hosts — registration would lock every `*.connlabai.com` subdomain).

- [ ] **Step 1: Rewrite the security-headers block in `frontend/nginx.conf`.**

  Replace lines 24–28 (the existing 4-line security-headers block) with:

  ```nginx
    # Security headers — see docs/superpowers/plans/2026-05-14-security-hardening-p5-...md §T7.
    #
    # script-src 'self'       — block all inline + remote JS (no CDN scripts allowed).
    # style-src 'unsafe-inline' — required for Radix UI / recharts inline `style` attrs (D3).
    # frame-ancestors 'none'  — no embedding (also enforced by X-Frame-Options for legacy UAs).
    # upgrade-insecure-requests — auto-upgrade any http:// references in the bundle.
    # HSTS preload — header advertises preload eligibility; apex registration is a separate
    #                operator decision (D4) and is NOT done by this chart.
    add_header X-Content-Type-Options       "nosniff"                              always;
    add_header X-Frame-Options              "DENY"                                 always;
    add_header Referrer-Policy              "strict-origin-when-cross-origin"      always;
    add_header Content-Security-Policy      "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'; upgrade-insecure-requests" always;
    add_header Permissions-Policy           "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()" always;
    add_header Cross-Origin-Opener-Policy   "same-origin"                          always;
    add_header Cross-Origin-Resource-Policy "same-origin"                          always;
    add_header Strict-Transport-Security    "max-age=63072000; includeSubDomains; preload" always;
  ```

  The `always` keyword on each `add_header` forces the header to be set even on 4xx/5xx responses (default nginx behaviour skips them on error responses).

- [ ] **Step 2: Confirm the nginx config is syntactically valid via the built image.**

  ```bash
  cd frontend
  docker build -t lolday-frontend:p5-t7-test .
  # Run the container with port-forward to localhost:8080
  docker run --rm -d -p 8888:8080 --name lolday-fe-p5 lolday-frontend:p5-t7-test
  sleep 2
  curl -sI http://localhost:8888/ | grep -iE '^(content-security-policy|permissions-policy|cross-origin-|strict-transport)'
  docker stop lolday-fe-p5
  ```

  Expected output (header values trimmed):

  ```
  Content-Security-Policy: default-src 'self'; script-src 'self'; ...
  Permissions-Policy: camera=(), microphone=(), ...
  Cross-Origin-Opener-Policy: same-origin
  Cross-Origin-Resource-Policy: same-origin
  Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
  ```

- [ ] **Step 3: Smoke-test the SPA in a browser to surface CSP breakage early.**

  With the container still running, open `http://localhost:8888/` in a browser, navigate through:
  - the sidebar (Radix UI primitives)
  - any page with a chart (Detector / Job runs view — recharts)
  - any modal / dropdown (Radix DropdownMenu, Dialog)

  In DevTools Console, watch for `Refused to apply inline style because it violates the following Content Security Policy directive` — if any appear, T7 has misconfigured CSP. **Stop and re-verify** before committing.

  Note: connecting to a real backend is not required for this CSP smoke test — the goal is to confirm the React tree mounts and Radix/recharts render. If `connect-src` triggers a console warning because the SPA tries to call `/api/v1/users/me` (which isn't proxied in this isolated container), that is expected and not a CSP failure of T7.

- [ ] **Step 4: Commit.**

  ```bash
  git add frontend/nginx.conf
  git commit -m "feat(frontend): hardening headers — CSP + Permissions-Policy + COOP/CORP + HSTS [F-csp-headers]"
  ```

---

## Task 8: [F-sourcemaps] Hidden source maps + strip from runtime image + upload to GHA artifact

**Findings:** F-sourcemaps (MEDIUM). Recommended model: **opus** (touches Vite config, Dockerfile two-stage flow, GHA workflow).

**Files:**

- Modify: `frontend/vite.config.ts:23` (`sourcemap: true` → `sourcemap: "hidden"`)
- Modify: `frontend/Dockerfile:19` (append `RUN find /usr/share/nginx/html -name '*.map' -delete` AFTER the `COPY --from=build` line)
- Modify: `.github/workflows/images.yml` (frontend matrix entry: add `--target=build --output type=local,dest=./fe-build-out` extraction step + `actions/upload-artifact` step)

**Rationale:** Vite's default production setting is `sourcemap: false`, but the lolday config has explicitly enabled `true` since the early phases (Phase 7 era), which means every prod runtime image carries `dist/*.map` files served at the same URLs as the JS bundles. A determined attacker can pull the maps from the bundled SPA and reconstruct readable source — bypassing minification's modest deterrent. `"hidden"` keeps the maps useful for crash-symbolicator workflows but strips the `//# sourceMappingURL=` comment from the bundled JS, so a browser-tab attacker can't auto-discover the map URL. The Dockerfile delete strips the `.map` files from the runtime image entirely. The GHA artifact upload (per-build, 14 d retention) keeps maps retrievable by the operator if needed.

The maps are extracted via a separate `docker buildx build --target=build --output type=local,dest=...` invocation — the maps live inside the build stage AFTER `pnpm run build`. The delete step in the serve stage removes maps from the runtime layer only (build stage retains them; the extraction step reads them).

- [ ] **Step 1: Modify `frontend/vite.config.ts`.**

  Change line 23 from:

  ```ts
    sourcemap: true,
  ```

  to:

  ```ts
    // F-sourcemaps (security-hardening P5): "hidden" emits *.map files
    // for crash symbolication but strips the `//# sourceMappingURL=` hint
    // from the bundled JS, so browser-tab attackers can't auto-discover
    // the map URL. The Dockerfile (serve stage) then deletes the .map
    // files from the runtime image; CI uploads them to a GHA artifact.
    sourcemap: "hidden",
  ```

- [ ] **Step 2: Modify `frontend/Dockerfile`.**

  After line 19 (`COPY --from=build /app/dist /usr/share/nginx/html`), add:

  ```dockerfile
  # F-sourcemaps: strip *.map from runtime image. The build stage retains
  # them so CI can extract via `docker buildx build --target=build
  # --output type=local,dest=...`.
  USER root
  RUN find /usr/share/nginx/html -name '*.map' -delete
  USER 101
  ```

  The `nginxinc/nginx-unprivileged` base image runs as UID 101; the temporary `USER root` / `USER 101` toggle is required because the original `COPY` placed files owned by root, and the unprivileged user can't delete them. The trailing `USER 101` restores the base image's runtime user.

- [ ] **Step 3: Modify `.github/workflows/images.yml` — frontend matrix only.**

  Locate the frontend matrix entry (matrix value `frontend`). After the existing buildx-build step (which produces the GHCR push), add:

  ```yaml
  # F-sourcemaps (security-hardening P5): extract maps from build stage
  # via target/output-local, then upload as a per-build artifact.
  - name: Extract source maps from build stage (frontend only)
    if: matrix.image == 'frontend'
    uses: docker/build-push-action@<EXISTING_DOCKER_BUILD_PUSH_SHA> # same SHA as the main step
    with:
      context: ./frontend
      target: build
      outputs: type=local,dest=./fe-build-out
      cache-from: type=gha,scope=frontend-${{ github.ref_name }}-buildstage

  - name: Upload frontend source maps to artifact (frontend only)
    if: matrix.image == 'frontend'
    uses: actions/upload-artifact@<UPLOAD_ARTIFACT_SHA> # v4.6.2 (D5)
    with:
      name: frontend-sourcemaps-${{ github.sha }}
      path: fe-build-out/app/dist/**/*.map
      retention-days: 14
      if-no-files-found: error
  ```

  Both new steps gate on `if: matrix.image == 'frontend'` so the workflow keeps backend / mlflow-server / pytorch-cu12-base builds unchanged.

  `<EXISTING_DOCKER_BUILD_PUSH_SHA>` is the SHA already used by the previous `docker/build-push-action` step in the same workflow — copy verbatim, do not re-pin.

- [ ] **Step 4: Pin `actions/upload-artifact` SHA.**

  ```bash
  TAG=v4.6.2
  SHA=$(gh api repos/actions/upload-artifact/git/refs/tags/$TAG --jq .object.sha)
  echo "$SHA  # $TAG"
  ```

  Replace `<UPLOAD_ARTIFACT_SHA>` in the workflow with the captured 40-char SHA. Add the same-line comment ` # v4.6.2` per `.claude/rules/github-actions.md` §Action pinning.

  If `v4.6.2` does not exist at execution time, capture the latest stable v4.x: `gh api repos/actions/upload-artifact/releases/latest --jq .tag_name`.

- [ ] **Step 5: Verify locally that the Dockerfile build still succeeds + runtime has no maps.**

  ```bash
  cd frontend
  docker build -t lolday-frontend:p5-t8-test .

  # Confirm the runtime image has zero .map files.
  docker run --rm --entrypoint sh lolday-frontend:p5-t8-test \
    -c "find /usr/share/nginx/html -name '*.map' | wc -l"
  ```

  Expected: `0`.

  Also confirm the build stage retains maps:

  ```bash
  docker buildx build --target build --output type=local,dest=./fe-build-out -f Dockerfile .
  find ./fe-build-out -name '*.map' | head
  rm -rf ./fe-build-out
  ```

  Expected: at least one `.map` file listed.

- [ ] **Step 6: Run the standard pre-commit gate.**

  ```bash
  pre-commit run --all-files
  ```

  Expected: clean. The new workflow change should pass yaml lint + actionlint rules (note: `actionlint` is not in this repo's pre-commit; the GHA `lint.yml` consumes the workflow itself).

- [ ] **Step 7: Commit.**

  ```bash
  git add frontend/vite.config.ts frontend/Dockerfile .github/workflows/images.yml
  git commit -m "feat(frontend): hidden source maps + strip from runtime image [F-sourcemaps]"
  ```

---

## Task 9: [L-cookie-attrs] Secure + SameSite=Lax on sidebar cookie

**Findings:** L-cookie-attrs (LOW). Recommended model: **sonnet** (one-line edit + e2e snapshot).

**Files:**

- Modify: `frontend/src/components/ui/sidebar.tsx:91` (cookie write-line)

**Rationale:** The shadcn/ui sidebar template persists open/closed state to `document.cookie` with no `Secure` / `SameSite` attribute. The cookie is non-sensitive (boolean UI state) but the lack of attributes is a Mozilla Observatory gap. `Secure` ensures the cookie is only sent over HTTPS (lolday is always HTTPS via Cloudflare Tunnel); `SameSite=Lax` blocks the cookie from being attached to cross-origin requests. Both are mainstream defaults — there is no functional regression because the cookie was never relied on cross-origin.

- [ ] **Step 1: Apply the change.**

  In `frontend/src/components/ui/sidebar.tsx`, line 91, replace:

  ```tsx
  document.cookie = `${SIDEBAR_COOKIE_NAME}=${openState}; path=/; max-age=${SIDEBAR_COOKIE_MAX_AGE}`;
  ```

  with:

  ```tsx
  // L-cookie-attrs (security-hardening P5): Secure + SameSite=Lax.
  // Cookie is non-sensitive UI state, but attributes are baseline.
  document.cookie = `${SIDEBAR_COOKIE_NAME}=${openState}; path=/; max-age=${SIDEBAR_COOKIE_MAX_AGE}; Secure; SameSite=Lax`;
  ```

- [ ] **Step 2: Verify the change does not break Playwright sidebar tests (if any).**

  ```bash
  cd frontend && pnpm test -- sidebar 2>&1 | tail -10
  ```

  Expected: no sidebar-named test failures. `Secure` is enforced by browsers only on HTTPS pages; under Playwright running against `http://localhost:5173`, the cookie still gets set (with the attribute), so behavior is unchanged in tests.

- [ ] **Step 3: Commit.**

  ```bash
  git add frontend/src/components/ui/sidebar.tsx
  git commit -m "fix(frontend): Secure + SameSite=Lax on sidebar cookie [L-cookie-attrs]"
  ```

---

## Task 10: [L-detector-desc-sanitize] Strip raw HTML / Markdown link syntax from detector description

**Findings:** L-detector-desc-sanitize (LOW). Recommended model: **sonnet** (small helper + register-route hook + tests).

**Files:**

- Modify: `backend/app/routers/detectors.py:199-254` (register endpoint — sanitize description before INSERT)
- Test: `backend/tests/test_detectors_description_sanitize.py` (new file)

**Rationale:** Detector descriptions come from the registered repo's `pyproject.toml` `project.description` field. The frontend renders descriptions in detector cards and listing tables. Today, a malicious detector author can put raw `<script>`, `<iframe>`, or Markdown link syntax (`[label](javascript:...)`) into `project.description`, and the description renders unescaped in the SPA. React's default JSX text rendering escapes HTML — so `<script>...</script>` is rendered as visible text, not executed — but `dangerouslySetInnerHTML` use elsewhere (or future i18n templating) could resurrect the vector. Strip at registration time for defense-in-depth.

The sanitizer is a small allowlist function — strip three specific patterns:

1. `<script>...</script>` (and self-closing variants)
2. `<iframe>...</iframe>`
3. `[label](url)` Markdown link syntax (entire match removed; aggressive but unambiguous)

Anything else (plain text, regular Markdown like `**bold**`, parentheses without preceding `[]`) passes through unchanged. The function is centralized at the register endpoint — detector description updates via PATCH already pass through the same router, so adding the same sanitization there is a one-line extension.

- [ ] **Step 1: Write the failing test.**

  Create `backend/tests/test_detectors_description_sanitize.py`:

  ```python
  """Detector description must be stripped of <script>, <iframe>, and Markdown link syntax."""

  import pytest


  @pytest.mark.parametrize(
      "raw,expected",
      [
          ("plain text", "plain text"),
          ("**markdown bold ok**", "**markdown bold ok**"),
          ("<script>alert(1)</script>safe", "safe"),
          ("<SCRIPT>alert(1)</SCRIPT>safe", "safe"),  # case-insensitive
          ("a<script>b</script>c", "ac"),
          ("a<iframe src='x'></iframe>z", "az"),
          ("see [docs](https://example.com)", "see "),
          ("see [docs](javascript:alert(1))", "see "),
          ("nested [[a](b)] case", "nested [] case"),  # explanation: inner [a](b) stripped
          ("no link here (a)", "no link here (a)"),  # plain parens preserved
          ("", ""),
          (None, None),
      ],
  )
  def test_sanitize_detector_description(raw, expected):
      from app.routers.detectors import sanitize_detector_description
      assert sanitize_detector_description(raw) == expected
  ```

- [ ] **Step 2: Run test to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_detectors_description_sanitize.py -v
  ```

  Expected: FAIL — `ImportError` on `sanitize_detector_description`.

- [ ] **Step 3: Add `sanitize_detector_description()` near the top of `backend/app/routers/detectors.py`.**

  After the existing top-of-file imports + module-level constants:

  ```python
  import re

  _RE_SCRIPT = re.compile(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL)
  _RE_IFRAME = re.compile(r"<\s*iframe\b[^>]*>.*?<\s*/\s*iframe\s*>", re.IGNORECASE | re.DOTALL)
  _RE_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")


  def sanitize_detector_description(value: str | None) -> str | None:
      """Strip <script>, <iframe>, and Markdown link syntax from a detector description.

      L-detector-desc-sanitize (security-hardening P5). The description is
      sourced from the detector repo's pyproject.toml project.description;
      authors are not adversarial today but the field is rendered in the
      SPA. Defense-in-depth.
      """
      if value is None:
          return None
      result = _RE_SCRIPT.sub("", value)
      result = _RE_IFRAME.sub("", result)
      result = _RE_MD_LINK.sub("", result)
      return result
  ```

- [ ] **Step 4: Apply sanitization in the register endpoint.**

  In `backend/app/routers/detectors.py::register` (around line 232), change:

  ```python
      description = meta["description"]
  ```

  to:

  ```python
      description = sanitize_detector_description(meta["description"])
  ```

  Repeat in `update_detector` (around line 299–300) for PATCH:

  ```python
      if body.description is not None:
          detector.description = sanitize_detector_description(body.description)
  ```

- [ ] **Step 5: Run tests to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_detectors_description_sanitize.py -v
  ```

  Expected: PASS.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/app/routers/detectors.py backend/tests/test_detectors_description_sanitize.py
  git commit -m "fix(detectors): sanitize description at register + update [L-detector-desc-sanitize]"
  ```

---

## Task 11: [L-team-domain-validator] Pydantic field_validator for CF_ACCESS_TEAM_DOMAIN

**Findings:** L-team-domain-validator (LOW). Recommended model: **sonnet** (single-field validator + paired config-validation tests).

**Files:**

- Modify: `backend/app/config.py:113-115` (add `field_validator` next to the existing model_validators)
- Test: `backend/tests/test_config_validation.py` (extend the existing file)

**Rationale:** `CF_ACCESS_TEAM_DOMAIN` becomes part of the JWKS URL (`https://{CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs`) and the JWT issuer claim. A malformed value (`https://foo` accidentally pasted into the env var) makes the JWKS fetch path go to `https://https://foo/cdn-cgi/...` and the issuer comparison silently fails. The validator enforces a hostname shape — `^[a-z0-9-]+(\.[a-z0-9-]+)+$` (one or more dot-separated labels of lowercase / digits / hyphen) — at boot, so the misconfiguration is a CrashLoopBackOff with a clear message instead of every request returning 401.

Empty string is still allowed (matches the existing default behavior of `CF_ACCESS_TEAM_DOMAIN: str = ""` — required only in `ENVIRONMENT=production` via the existing `validate_sso_config` model_validator at lines 131–148). The new validator is shape-only.

- [ ] **Step 1: Write the failing tests.**

  Append to `backend/tests/test_config_validation.py`:

  ```python
  import pytest
  from pydantic import ValidationError


  def test_cf_access_team_domain_accepts_valid_hostname(monkeypatch):
      """A normal Cloudflare Access team domain must validate."""
      monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
      monkeypatch.setenv("ENVIRONMENT", "development")  # bypass the production-only model_validator
      from app.config import Settings
      s = Settings()
      assert s.CF_ACCESS_TEAM_DOMAIN == "bolin8017.cloudflareaccess.com"


  def test_cf_access_team_domain_accepts_empty_string(monkeypatch):
      """Empty string is the default (development bypass); must not raise."""
      monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "")
      monkeypatch.setenv("ENVIRONMENT", "development")
      from app.config import Settings
      s = Settings()
      assert s.CF_ACCESS_TEAM_DOMAIN == ""


  @pytest.mark.parametrize(
      "bad",
      [
          "https://foo.example.com",      # scheme leaked in
          "foo.example.com/",              # trailing slash
          "FOO.EXAMPLE.COM",               # uppercase
          "foo_example.com",               # underscore not in [a-z0-9-]
          "foo example.com",               # space
          ".example.com",                  # leading dot
          "example.com.",                  # trailing dot
          "example",                       # no dot
          "@bad",                          # other junk
      ],
  )
  def test_cf_access_team_domain_rejects_invalid(monkeypatch, bad):
      monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", bad)
      monkeypatch.setenv("ENVIRONMENT", "development")
      from app.config import Settings
      with pytest.raises(ValidationError) as ei:
          Settings()
      assert "CF_ACCESS_TEAM_DOMAIN" in str(ei.value)
  ```

- [ ] **Step 2: Run tests to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_config_validation.py -v -k cf_access_team_domain
  ```

  Expected: FAIL — none of the rejects-invalid cases raise; the accepts cases also fail because no validator exists yet (none currently rejects bad shape).

- [ ] **Step 3: Add the field_validator to `backend/app/config.py`.**

  Insert immediately before the existing `_split_fernet_keys` validator (currently at line 123):

  ```python
      @field_validator("CF_ACCESS_TEAM_DOMAIN")
      @classmethod
      def _validate_cf_access_team_domain(cls, v: str) -> str:
          """L-team-domain-validator (security-hardening P5).

          Enforce a hostname shape so a malformed value (typo, scheme
          leaked in) is a CrashLoopBackOff at boot rather than every
          request returning 401 with an obscure JWKS-lookup error.
          Empty string passes (used in dev / test where the production
          model_validator is bypassed by ENVIRONMENT != 'production').
          """
          import re
          if v == "":
              return v
          if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", v):
              raise ValueError(
                  f"CF_ACCESS_TEAM_DOMAIN={v!r} is not a valid hostname "
                  "(expected lowercase dot-separated labels, e.g. "
                  "'bolin8017.cloudflareaccess.com'). Verify the env "
                  "var did not accidentally include a scheme or path."
              )
          return v
  ```

- [ ] **Step 4: Run tests to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_config_validation.py -v -k cf_access_team_domain
  ```

  Expected: all PASS.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/app/config.py backend/tests/test_config_validation.py
  git commit -m "feat(config): validate CF_ACCESS_TEAM_DOMAIN shape [L-team-domain-validator]"
  ```

---

## P5 Done

After Task 11 lands, verify the whole phase end-to-end:

- [ ] **Step A: Full backend test suite.**

  ```bash
  cd backend && uv run pytest -q 2>&1 | tail -5
  ```

  Expected: 732 baseline + new tests added across T1, T2, T3, T5, T6, T10, T11 ≈ 744–750 passed. No failures, no skips beyond the pre-existing baseline.

- [ ] **Step B: helm lint (post-P5).**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`. The three new Alertmanager rules added in T1 / T2 / T4 should pass the kube-prometheus-stack PrometheusRule template validation.

- [ ] **Step C: Frontend build + runtime image audit (acceptance criterion #4).**

  ```bash
  cd frontend
  pnpm install --frozen-lockfile
  pnpm build
  # Maps exist in dist/ (so CI can extract them)
  find dist -name '*.map' | head -3

  docker build -t lolday-frontend:p5-final .
  # Maps MUST NOT exist in the runtime image
  docker run --rm --entrypoint sh lolday-frontend:p5-final \
    -c "find /usr/share/nginx/html -name '*.map' | wc -l"
  ```

  Expected: `pnpm build` `dist/` lists ≥ 1 map; runtime image find returns `0`.

- [ ] **Step D: Live security-header audit (acceptance criterion #3).**

  After `bash scripts/deploy.sh` has shipped the chart:

  ```bash
  curl -sI https://lolday.connlabai.com/ \
    | grep -iE '^(content-security-policy|permissions-policy|cross-origin-|strict-transport|x-frame-options|x-content-type-options|referrer-policy):' \
    | wc -l
  ```

  Expected: `8` (CSP + Permissions-Policy + COOP + CORP + HSTS + X-Frame-Options + X-Content-Type-Options + Referrer-Policy).

- [ ] **Step E: Audit log live-write smoke (acceptance criterion #1).**

  ```bash
  # As an admin, demote a test user
  curl -sf -X PATCH https://lolday.connlabai.com/api/v1/admin/users/<TEST_USER_UUID> \
    --cookie "CF_Authorization=<admin JWT>" \
    -H 'content-type: application/json' \
    -d '{"role":"user"}'

  # Confirm the row exists
  kubectl -n lolday exec deploy/postgresql -- psql -U lolday -d lolday -c \
    "SELECT actor_id, action, target_type, target_id, before_jsonb, after_jsonb, ts \
     FROM audit_log ORDER BY ts DESC LIMIT 5;"
  ```

  Expected: the patch returns 200; the SELECT returns the new row with `action='admin.role_change'` + the expected before/after dicts.

- [ ] **Step F: Auth failure metric + alert smoke (acceptance criterion #2).**

  ```bash
  # Submit 5 invalid JWTs in < 10s
  for i in $(seq 1 5); do
    curl -s -o /dev/null -w '%{http_code}\n' \
      -H 'Cf-Access-Jwt-Assertion: invalid.jwt.value' \
      https://lolday.connlabai.com/api/v1/users/me
  done

  # Confirm the counter incremented (port-forward Prometheus from cluster)
  kubectl -n monitoring port-forward svc/kps-prometheus 9090:9090 &
  sleep 2
  curl -s 'http://localhost:9090/api/v1/query?query=lolday_auth_failure_total{reason="invalid_signature"}' \
    | jq '.data.result[0].value[1]'
  ```

  Expected: 5 × 401 responses; the Prometheus query returns a value ≥ 5. After 5 minutes of sustained traffic above 0.5/s, `LoldayAuthFailureSpike` fires (visible in Alertmanager UI / Spidey Warnings channel).

- [ ] **Step G: pre-commit on all files.**

  ```bash
  pre-commit run --all-files
  ```

  Expected: clean. **Do NOT use `--no-verify`** (per project hard rule).

- [ ] **Step H: Cross-check finding IDs in commit history.**

  ```bash
  git log --oneline main..HEAD | grep -oE '\[[A-Z][^]]+\]' | tr ',' '\n' | sort -u | tr -d '[]'
  ```

  Expected output (sorted unique set):

  ```
  F-csp-headers
  F-sourcemaps
  H-27
  L-cookie-attrs
  L-detector-desc-sanitize
  L-discord-alert
  L-event-broker-drops
  L-team-domain-validator
  M-audit-log
  M-jwt-email-pii
  M-ratelimit-metric
  ```

  11 distinct IDs, one per task.

- [ ] **Step I: Open the PR.**

  Push the branch + `gh pr create --base main`. PR body must call out:
  - **New DB migration:** `audit_log` table. `alembic-upgrade-hook` Job runs it on `helm upgrade`; if rolled back, downgrade is supported but P5 acceptance assumes forward-only (per `.claude/rules/alembic-migrations.md`).
  - **Three new Alertmanager rules:** `LoldayAuthFailureSpike` / `LoldayRateLimitSpike` / `LoldayDiscordNotifyFailing` route to Spidey Warnings (severity=warning); Captain Hook is untouched.
  - **New nginx hardening headers:** browsers will start rejecting any future inline `<script>` or non-`self` CDN reference at runtime. Frontend additions that need CDN scripts must be reviewed against the CSP block (see plan §D3).
  - **No data migrations + no detector behavior change.** Builds, jobs, and MLflow runs continue without disruption.
  - **Operator action post-merge — register HSTS preload (optional):** the response now advertises `preload` eligibility. Submitting `lolday.connlabai.com` (or the connlabai apex) at <https://hstspreload.org> is a separate operator decision; the chart does NOT trigger registration (D4).

---

## Notes for the implementer

- **Prometheus `REGISTRY.get_sample_value` returns None pre-first-increment.** The `_read()` helper in T1 / T2 / T3 tests normalises None → 0.0. Always use this helper, not bare `get_sample_value`, otherwise the diff-by-1 assertion blows up on the first run.
- **`prometheus_client.Counter` is process-global by default.** A test that increments the Counter affects every other test in the same process. The `before / after` diff pattern in T1–T3 is robust against this. Do NOT add `REGISTRY.unregister(...)` between tests — that mutates global state and breaks isolation when tests run in parallel.
- **Alembic autogenerate is unreliable for JSONB + `with_variant`.** When T5 Step 5 runs `alembic revision --autogenerate`, the generated migration is likely to drop the `with_variant(JSONB(), "postgresql")` clause on `before_jsonb` / `after_jsonb` and produce a plain `JSON` column. Manually review the generated file and add the variant back. Reference: `backend/migrations/versions/1afdf61e18f9_add_maldet_version_to_detector_version.py` shows a clean recent migration shape.
- **AsyncSession + lazy load gotcha (T5 admin.py edit).** The existing `update_user` code reads `target.role.value` to log the role change. `target.role` is an enum column (eager-loaded), not a relationship, so no lazy-load greenlet error. The audit row's `before_jsonb={"role": old_role.value}` follows the same eager-loaded path — safe.
- **CSP `style-src 'unsafe-inline'` is intentional (T7).** Do NOT trim it out in a future audit pass. Radix UI + recharts emit React `style={{...}}` inline attributes; nonce / hash CSP for SPA-built bundles is impractical. Industry standard. Tracked design decision D3.
- **HSTS preload directive is not registration (T7).** Shipping the directive in the response header is zero cost. Registering at hstspreload.org is a long-lived (6–12 month removal SLA) operator decision tied to the apex domain `connlabai.com`. D4.
- **Per-task TDD note.** T1 / T2 / T3 / T5 / T6 / T10 / T11 are backend code → TDD discipline applies (failing test first). T4 is alert-only (Helm template) — no Python test; verification is `helm lint` + `kubectl get prometheusrule` after deploy. T7 / T8 / T9 are frontend / Dockerfile / nginx — verification is `docker build` + `curl -I` + browser smoke.
- **Model selection per task** (recommended; pass via `--model` to subagent):
  - **sonnet** — T2, T3, T4, T6, T9, T10, T11 (single-file edits, one new helper, regression test)
  - **opus** — T1 (multi-file with metrics + auth surface + alert), T5 (new model + migration + 3 router insertions), T7 (CSP rewrite touches the prod surface), T8 (multi-stage Docker + GHA workflow + Vite config)

---

## Self-review (writing-plans skill)

**Spec coverage** — every P5 finding from spec §6.5 maps to exactly one task:

| Finding                  | Task |
| ------------------------ | ---- |
| H-27                     | T1   |
| M-audit-log              | T5   |
| M-ratelimit-metric       | T2   |
| M-jwt-email-pii          | T6   |
| F-sourcemaps             | T8   |
| F-csp-headers            | T7   |
| L-cookie-attrs           | T9   |
| L-discord-alert          | T4   |
| L-event-broker-drops     | T3   |
| L-detector-desc-sanitize | T10  |
| L-team-domain-validator  | T11  |

11 spec findings → 11 implementation tasks (1:1 mapping). All four spec-level acceptance criteria (§6.5) traceable:

| Spec acceptance                                                                                            | Plan check                          |
| ---------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| 1. `audit_log` table migration applied; PATCH writes a row visible via `SELECT * FROM audit_log`           | T5 Step 5 + Step 7 + P5 Done Step E |
| 2. 5 invalid JWTs → `lolday_auth_failure_total{reason="invalid_signature"} >= 5`; alert fires in `for: 5m` | T1 Step 4 + Step 6 + P5 Done Step F |
| 3. `curl -I https://lolday/` shows full security headers                                                   | T7 + P5 Done Step D                 |
| 4. `find frontend/dist -name '*.map'` after `pnpm build && docker build` is empty in runtime image         | T8 Step 5 + P5 Done Step C          |

**Placeholder scan:**

- `<REV>` placeholder in the alembic migration filename (T5 Step 5) — resolved by `alembic revision --autogenerate -m "add audit_log table"` at execution time. The plan instructs the implementer how to derive it.
- `<UPLOAD_ARTIFACT_SHA>` in T8 Step 3/4 — resolved via the explicit `gh api repos/actions/upload-artifact/git/refs/tags/v4.6.2 --jq .object.sha` command. Not a plan failure — the plan tells the implementer where to look. Same pattern P4 plan used for cosign / sbom-action SHA placeholders.
- `<EXISTING_DOCKER_BUILD_PUSH_SHA>` in T8 Step 3 — instruct the implementer to copy verbatim from the existing line in `images.yml`. Not a discovery step.
- `<TEST_USER_UUID>` + `<admin JWT>` in P5 Done Step E — operator-supplied at smoke-test time. Comparable to the cosign verify operator step in P4 Done.
- No `TBD` / `implement later` / `add appropriate error handling` markers.

**Type consistency:**

- `AuditLog.before_jsonb` / `after_jsonb` — `dict[str, Any] | None` everywhere (model column, service function signature, router call sites). Pydantic/SQLAlchemy round-trip stays as `dict`.
- `AUTH_FAILURE_TOTAL.labels(reason=...)` — `reason` keyword consistent across all 4 failure branches. No positional / kwarg mix.
- `RATE_LIMIT_HITS_TOTAL.labels(prefix=...)` — `prefix` keyword consistent across both `rate_limit_user` and `rate_limit_ip` closures.
- `write_audit_log()` signature — kwargs (`actor_id=...`, `action=...`) consistent across all three router call sites in T5.
- `redact_email(value: str | None) -> str` — handles `None` returning `"<redacted-none>"`, never raises. Tests cover both branches.
- `sanitize_detector_description(value: str | None) -> str | None` — returns `None` when given `None` (matches the column's nullable shape). Tested.

**Known fragilities:**

- **T1 metric global registry.** Tests share the process-global Counter. Diff-by-N pattern (`before / after`) is robust. If a future test runner introduces test-level forking (pytest-xdist with `--forked`), the diff still works per-fork. No mitigation needed.
- **T5 audit row inside the resource transaction.** If the outer commit succeeds at the DB but `session.refresh()` raises immediately after (unusual), the audit row is committed but the response carries stale state. Acceptable — the audit row reflects the actual DB mutation. Mitigation: do not add a try/except.
- **T7 CSP browser breakage.** Despite the smoke test in T7 Step 3, a corner of the SPA may pull in an inline-script source map or a future CDN reference. Mitigation: roll the chart with a 5-minute soak; revert path is `git revert <T7 commit>` + `helm upgrade --reuse-values --version <previous>`.
- **T8 buildx target=build cache miss.** Extracting `--target=build` re-runs `pnpm build` if the GHA cache scope (`frontend-${{ github.ref_name }}-buildstage`) misses. First run after the change pays a 2-3 minute cost; subsequent runs hit cache. No correctness impact.
- **T11 hostname regex strictness.** The regex `^[a-z0-9-]+(\.[a-z0-9-]+)+$` does not allow Punycode (IDN). Cloudflare Access does not currently issue IDN team domains; if a future requirement emerges, replace with `idna.decode` + a slightly looser regex. Tracked.

**Deferred (NOT in P5):**

- Audit log retention / pruning. Spec acceptance is "row exists" — no retention policy. P6 follow-up candidate: an `audit_log` partition policy or a TTL VACUUM.
- AuditLog reader UI / admin route to query the table. Spec does not require it; psql is the read path for the audit period.
- Audit log column for `request_id` / `trace_id`. Lolday has no distributed-tracing today; adding the column without a producer is YAGNI.
- HSTS preload registration at hstspreload.org. Operator decision (D4); not part of the chart.

---

## Estimated effort breakdown

11 tasks, single-engineer (sonnet/opus mix per recommendation), TDD discipline:

| Chain                               | Tasks           | Effort      |
| ----------------------------------- | --------------- | ----------- |
| Observability (counters + alerts)   | T1, T2, T3, T4  | ~3 hrs      |
| Audit (model + migration + 3 sites) | T5              | ~3 hrs      |
| PII redaction                       | T6              | ~1 hr       |
| Frontend hardening                  | T7, T8, T9      | ~3 hrs      |
| Input validation                    | T10, T11        | ~1 hr       |
| Deploy + verify + PR                | P5 Done         | ~2 hrs      |
| **Total**                           | **11 + verify** | **~13 hrs** |

Slightly tighter than P4's 16-task scope. Aligns with spec's "~1 week" upper bound (1.5–2 working days of focused work).
