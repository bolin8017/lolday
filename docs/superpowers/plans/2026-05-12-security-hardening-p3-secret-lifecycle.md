# Security Hardening P3 — Secret Lifecycle Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every secret in the platform has a defined rotation cadence, no secret leaks into logs or argv, and the operator can rotate keys without re-issuing the entire encrypted-at-rest data set.

**Architecture:** Fifteen TDD-or-helm-test tasks against existing backend + chart + scripts. The Fernet rotation chain (T6–T10) is the heaviest single feature: it converts `TokenCipher` to `MultiFernet`, renames the `FERNET_KEY` singular env into a `FERNET_KEYS` whitespace-separated list, adds a Settings validator that hard-fails boot on the well-known test key, and ships an offline `app.scripts.rotate_fernet` re-encryption script + operator runbook. The Harbor robot chain (T13–T14) changes the long-lived `duration: -1` robot to 90 d and adds a reconciler that quarterly renews + force-rotates legacy `-1` robots in-place. The owner-reference chain (T11–T12) links `job-token-<id>` Secret lifetimes to their vcjob via `metadata.ownerReferences` plus a belt-and-suspenders reconciler sweep. Independent quick wins (T1–T5) and a one-time MinIO key rotation script (T15) round out the phase.

**Tech Stack:** FastAPI, Pydantic v2 + `pydantic-settings`, SQLAlchemy 2.0 async, cryptography (Fernet / MultiFernet), Helm 3, K3s, Volcano `Job` (`batch.volcano.sh/v1alpha1`), Harbor 2.x REST, MinIO `mc admin user svcacct`, `age` (operator-local).

**Source spec:** [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../specs/2026-05-12-security-hardening-design.md) §6.3.

**Finding IDs covered:** H-17, H-18, H-18a, H-19, H-22, M-deploy-from-literal, M-discord-log, M-token-secret-owner, M-pg-exporter, L-harbor-robot-rotate, L-minio-key-rotate (14 spec findings; H-17 + H-18 split across two tasks for TDD clarity = 15 implementation tasks).

---

## Pre-flight

- [ ] **Confirm clean working tree on `main`.**

  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  git status
  git rev-parse HEAD
  ```

  Expected: working tree clean (modulo `backend/kube-prometheus-stack/` untracked, which is unrelated); HEAD at `b8639ee` (post-P2 merge) or newer.

- [ ] **Confirm backend test baseline.**

  ```bash
  cd backend && uv run pytest -q
  ```

  Expected: green. Capture the passed-count for the post-phase delta.

- [ ] **Create the feature branch.**

  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  git checkout -b security-hardening-p3
  ```

  The plan itself is committed directly to `main` (continuation of the security spec). All P3 task commits land on `security-hardening-p3` and squash-merge back to `main` via a single PR per the P1/P2 pattern.

- [ ] **Confirm `helm lint` baseline.**

  After P2, helm lint requires these flags. Cache the command — every helm-lint step in this plan uses the same set:

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test \
    --set fernetKey=test \
    --set postgresql.auth.password=test \
    --set mlflow.auth.password=test \
    --set mlflow.db.password=test \
    --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test \
    --set grafana.adminPassword=test
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`. Note: `fernetKey` is the pre-T8 flag; T8 renames the chart key to `fernetKeys` and every helm-lint step after T8 uses `--set backend.fernetKeys=test` instead.

---

## Task 1: [M-discord-log] Redact Discord webhook URL in notify failure log

**Findings:** M-discord-log (MEDIUM). Recommended model: **sonnet** (small, isolated change).

**Files:**

- Modify: `backend/app/services/notify.py:33-45`
- Modify (add tests): `backend/tests/test_services_notify.py`

**Rationale:** Today `post_webhook` uses `logger.exception("Discord webhook delivery failed")` on any non-2xx or transport error. `logger.exception` includes the full traceback, which under `httpx`'s `HTTPStatusError` ends with `request._url = "https://discord.com/api/webhooks/<channel-id>/<token>"` — the webhook URL is also the secret. Loki keeps every backend log for 14 days, so a single 500 from Discord burns the webhook into log storage that operators routinely search. Switch to `logger.warning` with only `status` + `host` (no path, no token). The metric `BACKEND_ERRORS{stage="discord_notify"}` still increments unchanged.

- [ ] **Step 1: Write the failing tests.**

  Append to `backend/tests/test_services_notify.py`:

  ```python
  import logging


  @pytest.mark.asyncio
  async def test_post_webhook_500_logs_host_not_url(monkeypatch, caplog):
      """M-discord-log: failure log must contain the host + status, never the
      webhook token or path."""
      monkeypatch.setattr(
          "app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", WEBHOOK
      )
      with respx.mock() as mock:
          mock.post(WEBHOOK).mock(return_value=httpx.Response(500))
          with caplog.at_level(logging.WARNING, logger="app.services.notify"):
              await notify.post_webhook({"content": "hi"})
      messages = " ".join(r.getMessage() for r in caplog.records)
      # Token + path are the secret part of the URL.
      assert "xyz" not in messages
      assert "/api/webhooks/" not in messages
      # Host + status are useful for ops debug.
      assert "discord.test" in messages
      assert "status=500" in messages


  @pytest.mark.asyncio
  async def test_post_webhook_network_error_logs_host_not_url(monkeypatch, caplog):
      """A ConnectError carries the URL in its repr — make sure we don't leak it."""
      monkeypatch.setattr(
          "app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", WEBHOOK
      )
      with respx.mock() as mock:
          mock.post(WEBHOOK).mock(side_effect=httpx.ConnectError("boom"))
          with caplog.at_level(logging.WARNING, logger="app.services.notify"):
              await notify.post_webhook({"content": "hi"})
      messages = " ".join(r.getMessage() for r in caplog.records)
      assert "xyz" not in messages
      assert "/api/webhooks/" not in messages
      assert "discord.test" in messages
      assert "error=ConnectError" in messages
  ```

- [ ] **Step 2: Run the failing tests.**

  ```bash
  cd backend && uv run pytest tests/test_services_notify.py::test_post_webhook_500_logs_host_not_url tests/test_services_notify.py::test_post_webhook_network_error_logs_host_not_url -v
  ```

  Expected: both FAIL — the assertion `"/api/webhooks/" not in messages` fails because today's `logger.exception` includes the URL in the traceback.

- [ ] **Step 3: Implement the redaction.**

  Replace `post_webhook` in `backend/app/services/notify.py` (preserve the docstring and the `notify_*` functions below):

  ```python
  from urllib.parse import urlparse


  async def post_webhook(payload: dict) -> None:
      url = settings.DISCORD_WEBHOOK_URL_EVENTS
      if not url:
          return
      host = urlparse(url).hostname or "?"
      try:
          async with httpx.AsyncClient(
              timeout=settings.DISCORD_HTTP_TIMEOUT_SECONDS
          ) as client:
              resp = await client.post(url, json=payload)
              resp.raise_for_status()
      except httpx.HTTPStatusError as exc:
          BACKEND_ERRORS.labels(stage="discord_notify").inc()
          # M-discord-log: webhook URL is itself the secret — log host + status
          # only. Full path / token is the same value Discord uses to authenticate
          # the POST, so anything that lands in Loki is effectively the credential.
          logger.warning(
              "Discord notify failed: status=%s host=%s",
              exc.response.status_code,
              host,
          )
      except Exception as exc:
          BACKEND_ERRORS.labels(stage="discord_notify").inc()
          logger.warning(
              "Discord notify failed: error=%s host=%s",
              type(exc).__name__,
              host,
          )
  ```

- [ ] **Step 4: Run the tests.**

  ```bash
  cd backend && uv run pytest tests/test_services_notify.py -v
  ```

  Expected: all notify tests pass, including the two new ones.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/app/services/notify.py backend/tests/test_services_notify.py
  git commit -m "$(cat <<'EOF'
  fix(backend): redact Discord webhook URL in notify failure log [M-discord-log]

  logger.exception attaches the full traceback (including the webhook URL,
  which is the secret) to every Discord delivery failure. Replace with
  logger.warning that emits status + host only — Loki keeps backend logs
  for 14 days, so even a single 500 burns the webhook into searchable storage.
  BACKEND_ERRORS{stage=discord_notify} increment unchanged.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 2: [M-pg-exporter] Switch postgres-exporter to `DATA_SOURCE_{USER,PASS,URI}`

**Findings:** M-pg-exporter (MEDIUM). Recommended model: **sonnet** (chart-only).

**Files:**

- Modify: `charts/lolday/templates/monitoring/postgres-exporter.yaml`

**Rationale:** The `DATA_SOURCE_NAME` env carries the full DSN `postgresql://user:pass@host/db?sslmode=disable` — the password is part of the key. Anyone with namespace-scoped `secrets get` can read the raw Secret, but the failure mode that motivates this finding is incidental: `kubectl describe deployment postgres-exporter` shows `Environment: DATA_SOURCE_NAME: <set to ...>` without the value, but the secret-key name itself reveals the embedded-password convention; switching to `DATA_SOURCE_USER` / `DATA_SOURCE_PASS` / `DATA_SOURCE_URI` separates the secret value (PASS) from the rest, so `kubectl describe secret postgres-exporter-db` shows `DATA_SOURCE_URI: <empty>` / `DATA_SOURCE_USER: <empty>` (Kubernetes redacts all values, but only the URI's key name remains; user is a literal namespace identifier, not a credential). `sslmode=disable` stays — in-cluster TLS is tracked under §9 risk register.

- [ ] **Step 1: Inspect the existing manifest.**

  ```bash
  helm template charts/lolday \
    --set redis.auth.password=test --set fernetKey=test --set postgresql.auth.password=test \
    --set mlflow.auth.password=test --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test 2>/dev/null \
    | sed -n '/kind: Secret$/,/^---/{/postgres-exporter-db/,/^---/p}'
  ```

  Expected: shows the `postgres-exporter-db` Secret with `DATA_SOURCE_NAME: "postgresql://postgres_exporter:test@..."` + `password: test`. After this task the `DATA_SOURCE_NAME` key disappears.

- [ ] **Step 2: Modify the Secret block.**

  In `charts/lolday/templates/monitoring/postgres-exporter.yaml:1-16`, replace the `stringData` block with:

  ```yaml
  type: Opaque
  stringData:
    # M-pg-exporter: the postgres_exporter binary accepts EITHER
    #   DATA_SOURCE_NAME=<full DSN with embedded password>
    # OR the split form:
    #   DATA_SOURCE_USER + DATA_SOURCE_PASS + DATA_SOURCE_URI (host:port/db?sslmode=disable)
    # The split form keeps the password in its own key so a future
    # mount-as-files refactor can ship USER + URI to ConfigMap and leave
    # PASS as the only Secret field. Sslmode=disable is tracked under
    # docs/superpowers/specs/2026-05-12-security-hardening-design.md §9.
    DATA_SOURCE_USER: "postgres_exporter"
    DATA_SOURCE_PASS:
      {
        {
          required "monitoring.postgresExporter.password must be set" .Values.monitoring.postgresExporter.password | quote,
        },
      }
    DATA_SOURCE_URI: "postgresql.{{ .Values.global.namespace }}.svc:5432/{{ .Values.postgresql.auth.database }}?sslmode=disable"
    # ``password`` key preserved for postgres-exporter-initjob's secretKeyRef
    # — the init Job binds ``PGPASSWORD`` from this specific key, not from envFrom.
    password:
      {
        {
          required "monitoring.postgresExporter.password must be set" .Values.monitoring.postgresExporter.password | quote,
        },
      }
  ```

  Leave the Deployment + Service blocks below the `---` separator untouched: the Deployment still uses `envFrom: secretRef: name: postgres-exporter-db`, which now injects three named env vars (USER, PASS, URI) instead of one (NAME). `postgres_exporter` auto-detects which set is present.

- [ ] **Step 3: Helm-render and verify the keys.**

  ```bash
  helm template charts/lolday \
    --set redis.auth.password=test --set fernetKey=test --set postgresql.auth.password=test \
    --set mlflow.auth.password=test --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test 2>/dev/null \
    | sed -n '/postgres-exporter-db/,/^---/p' | head -20
  ```

  Expected: `stringData:` block contains `DATA_SOURCE_USER`, `DATA_SOURCE_PASS`, `DATA_SOURCE_URI`, `password` — and NO `DATA_SOURCE_NAME` line.

- [ ] **Step 4: helm lint.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set fernetKey=test --set postgresql.auth.password=test \
    --set mlflow.auth.password=test --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 5: Commit.**

  ```bash
  git add charts/lolday/templates/monitoring/postgres-exporter.yaml
  git commit -m "$(cat <<'EOF'
  fix(charts): split postgres-exporter DSN into USER/PASS/URI [M-pg-exporter]

  postgres_exporter accepts either DATA_SOURCE_NAME (full DSN with embedded
  password) or DATA_SOURCE_USER + DATA_SOURCE_PASS + DATA_SOURCE_URI. Switch
  to the split form so the password lives in its own Secret key — readying
  for a future mount-as-files refactor and matching the convention the rest
  of the chart already uses for Postgres credentials. password key preserved
  for postgres-exporter-initjob's PGPASSWORD secretKeyRef.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 3: [H-22] Document age-encrypted Cloudflare Access backups

**Findings:** H-22 (HIGH). Recommended model: **sonnet** (docs-only).

**Files:**

- Create: `docs/runbooks/cf-access-backups.md`
- Modify: `docs/operations.md` (the `.lolday-cloudflare-access-backups/` bullet)

**Rationale:** The operator periodically snapshots Cloudflare Access app/policy state into `.lolday-cloudflare-access-backups/<event>-<ts>.json` for audit. The directory is gitignored, but the cleartext JSON sits on the operator workstation indefinitely and reveals SSO architecture (rule IDs, identity-provider config, group claims) that should not be readable without a key. Encrypt with `age` (modern, ssh-key-compatible, single-binary), document the operator procedure, and have the operator delete the existing cleartext files. No code is touched.

- [ ] **Step 1: Verify the current state of the backups dir.**

  ```bash
  ls -la .lolday-cloudflare-access-backups/ 2>/dev/null || echo "(dir absent)"
  ```

  Expected on the operator workstation: at least one `*.json` file (e.g., `app-pre-otp-removal-20260422T122701Z.json`). On a fresh clone the dir may be absent — that's fine, the runbook still applies.

- [ ] **Step 2: Write the runbook.**

  Create `docs/runbooks/cf-access-backups.md`:

  ````markdown
  # Cloudflare Access backups — age-encrypted snapshots

  **Scope:** operator-local backups of Cloudflare Access app + policy state, kept
  for audit. Stored under `.lolday-cloudflare-access-backups/` (repo-root,
  gitignored). Every snapshot must be encrypted with [`age`](https://age-encryption.org/);
  cleartext `.json` files are forbidden.

  **Why this exists:** the snapshots reveal SSO architecture details — rule IDs,
  identity-provider configuration, OTP/email-binding state, group claims. An
  attacker who reads them learns how to craft a JWT that satisfies the live
  policy without ever talking to Cloudflare. The repo `.gitignore` keeps them
  out of git, but the operator workstation is the residual exposure surface.

  ## Prerequisites

  Install age (Ubuntu 24.04+):

  ```bash
  sudo apt install age   # or:  ~/.local/bin/age — download from github.com/FiloSottile/age/releases
  ```
  ````

  Generate (or import) an X25519 keypair, stored under `~/.config/age/`:

  ```bash
  mkdir -p ~/.config/age && chmod 700 ~/.config/age
  age-keygen -o ~/.config/age/lolday-cf-access.key
  chmod 600 ~/.config/age/lolday-cf-access.key
  ```

  Note the recipient line printed at the top of the keyfile (`# public key:
age1...`). Export it for convenience:

  ```bash
  export AGE_RECIPIENT="$(grep -oE 'age1[0-9a-z]+' ~/.config/age/lolday-cf-access.key | head -1)"
  ```

  Persist `AGE_RECIPIENT` in `~/.zshrc` so future invocations don't need to
  re-read the keyfile.

  ## Capture a new snapshot

  ```bash
  cd ~/Documents/repositories/lolday
  STAMP=$(date -u +%Y%m%dT%H%M%SZ)
  EVENT=otp-removal-pre  # short description; goes in the filename
  curl -sS -H "Authorization: Bearer $CF_API_TOKEN" \
    "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/access/apps" \
    | age -r "$AGE_RECIPIENT" > ".lolday-cloudflare-access-backups/app-$EVENT-$STAMP.json.age"
  curl -sS -H "Authorization: Bearer $CF_API_TOKEN" \
    "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/access/policies" \
    | age -r "$AGE_RECIPIENT" > ".lolday-cloudflare-access-backups/policy-$EVENT-$STAMP.json.age"
  ```

  The cleartext API response goes straight into age via stdin — never lands on disk.

  ## Read an existing snapshot

  ```bash
  age -d -i ~/.config/age/lolday-cf-access.key \
    .lolday-cloudflare-access-backups/app-otp-removal-pre-20260422T122701Z.json.age \
    | jq .
  ```

  ## Migrate existing cleartext snapshots

  Run once on the operator workstation:

  ```bash
  cd ~/Documents/repositories/lolday/.lolday-cloudflare-access-backups
  shopt -s nullglob
  for f in *.json; do
    age -r "$AGE_RECIPIENT" < "$f" > "$f.age" && shred -u "$f"
  done
  ```

  Verify: `ls *.json 2>/dev/null` returns nothing; `ls *.json.age` shows the
  encrypted files.

  ## Key management
  - The age key is operator-local. **Never commit it.** It sits in `~/.config/age/`
    under chmod 600.
  - For survivability, copy the keyfile to a second device (encrypted USB, password
    manager attachment). Losing the key means every existing `.json.age` is
    unrecoverable.
  - Rotation: generate a new keypair, re-encrypt every `.json.age` under the new
    recipient (`age -d -i OLD.key file.age | age -r NEW_RECIPIENT > file.age.tmp
&& mv file.age.tmp file.age`), then `shred -u OLD.key`. Update
    `AGE_RECIPIENT` in `~/.zshrc`.

  ## Why age and not GPG?

  age has a single binary, no agent / keyring machinery, X25519 keys that double
  as the encrypt + decrypt material, and no key-server dependency. The operator
  is one person; GPG's web-of-trust adds no value here.

  ```

  ```

- [ ] **Step 3: Update `docs/operations.md`.**

  Find the bullet currently reading:

  ```markdown
  - **`.lolday-cloudflare-access-backups/`** — directory of JSON snapshots of CF Access app/policy state (audit). Not consumed by any script.
  ```

  Replace with:

  ```markdown
  - **`.lolday-cloudflare-access-backups/`** — directory of age-encrypted (`.json.age`) snapshots of CF Access app/policy state (audit). Encrypt with `age -r $AGE_RECIPIENT < state.json > state.json.age` per [`docs/runbooks/cf-access-backups.md`](runbooks/cf-access-backups.md); cleartext `.json` is forbidden. Not consumed by any script.
  ```

- [ ] **Step 4: Cross-check no other doc still says "JSON snapshots".**

  ```bash
  grep -rn "lolday-cloudflare-access-backups\|JSON snapshots of CF Access" docs/
  ```

  Expected: only the operations.md edit + the new runbook.

- [ ] **Step 5: Operator action note (do NOT execute from the script).**

  Add the following to the PR body when the phase is opened:

  > After this PR is merged, the operator must run the migration block from
  > `docs/runbooks/cf-access-backups.md` § "Migrate existing cleartext snapshots"
  > on their workstation. The repo cannot do this — `.lolday-cloudflare-access-backups/`
  > is operator-local.

- [ ] **Step 6: Commit.**

  ```bash
  git add docs/runbooks/cf-access-backups.md docs/operations.md
  git commit -m "$(cat <<'EOF'
  docs(runbooks): require age-encrypted Cloudflare Access backups [H-22]

  .lolday-cloudflare-access-backups/ snapshots reveal SSO architecture
  (rule IDs, IdP config, OTP state) that should not sit cleartext on the
  operator workstation. Add docs/runbooks/cf-access-backups.md covering
  key setup, snapshot capture, read, migrate-existing-cleartext, and key
  rotation. Update operations.md to call out the .json.age requirement.

  Operator-action (out-of-band): re-encrypt the two existing snapshots
  per the runbook and shred -u the cleartext.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 4: [M-deploy-from-literal] Switch `kubectl create secret` to `--from-file` + `shred -u`

**Findings:** M-deploy-from-literal (MEDIUM). Recommended model: **sonnet**.

**Files:**

- Modify: `scripts/deploy.sh:175-196`

**Rationale:** `kubectl create secret --from-literal=KEY=$VALUE` places `$VALUE` directly into kubectl's argv. Anyone with `procfs` access on the operator workstation can read it from `/proc/<pid>/cmdline`; on multi-user systems this is `chmod 644` by default. The webhook URLs are themselves the credentials (anyone who possesses them can post to the channel). The `--from-file` form keeps the value on disk in a `mktemp -d` directory with chmod 600, never on the command line. `shred -u` overwrites + unlinks each file as soon as kubectl finishes; a `trap` on `EXIT` belt-and-suspenders the cleanup in case of mid-script abort. Pattern matches `scripts/recover-harbor.sh` (already uses tmp-file + shred for the harbor docker config).

- [ ] **Step 1: Inspect lines 175-196.**

  ```bash
  sed -n '175,196p' scripts/deploy.sh
  ```

  Expected: the three `kubectl create secret ... --from-literal=...` calls (two for `alertmanager-discord`, one for `discord-events`).

- [ ] **Step 2: Replace the block.**

  In `scripts/deploy.sh`, locate the section starting with the `# Phase 7.1: Alertmanager Discord webhook Secret.` comment (currently around line 175). Replace from that comment through the end of the `if [ -n "$DISCORD_WEBHOOK_URL_EVENTS" ]; then ... fi` block with:

  ```bash
  # M-deploy-from-literal: kubectl --from-literal puts the secret in argv
  # (/proc/<pid>/cmdline). Stage via mktemp + chmod 600 + shred -u, mirroring
  # scripts/recover-harbor.sh's harbor-push-cred handling.
  SECRET_TMP=$(mktemp -d)
  chmod 700 "$SECRET_TMP"
  # trap clears even on early failure (set -e fires before the manual shred lines).
  trap 'find "$SECRET_TMP" -type f -exec shred -u {} + 2>/dev/null; rmdir "$SECRET_TMP" 2>/dev/null || true' EXIT

  # Phase 7.1: Alertmanager Discord webhook Secret. Referenced by the
  # AlertmanagerConfig CR `discord-receivers` (see templates/monitoring/alertmanager-config-discord.yaml)
  # via apiURL.name/key SecretKeySelector, so the Prometheus Operator resolves
  # these webhook URLs when building the runtime Alertmanager config. Must exist
  # in the monitoring ns (same as Alertmanager pod + AC CR) before helm upgrade.
  printf '%s' "$DISCORD_WEBHOOK_URL_CRITICAL" > "$SECRET_TMP/webhook-url-critical"
  printf '%s' "$DISCORD_WEBHOOK_URL_WARNING"  > "$SECRET_TMP/webhook-url-warning"
  chmod 600 "$SECRET_TMP"/webhook-url-critical "$SECRET_TMP"/webhook-url-warning
  kubectl -n monitoring create secret generic alertmanager-discord \
    --from-file="$SECRET_TMP/webhook-url-critical" \
    --from-file="$SECRET_TMP/webhook-url-warning" \
    --dry-run=client -o yaml | kubectl apply -f -
  shred -u "$SECRET_TMP/webhook-url-critical" "$SECRET_TMP/webhook-url-warning"

  # Phase 7.4: backend reads DISCORD_WEBHOOK_URL_EVENTS from this Secret in the
  # release namespace. Create only if a value was supplied — empty value would
  # mask config errors (notify becomes silent no-op). The Deployment env binding
  # is `optional: true`, so absence of the Secret is also tolerated.
  if [ -n "$DISCORD_WEBHOOK_URL_EVENTS" ]; then
    printf '%s' "$DISCORD_WEBHOOK_URL_EVENTS" > "$SECRET_TMP/webhook-url"
    chmod 600 "$SECRET_TMP/webhook-url"
    kubectl -n lolday create secret generic discord-events \
      --from-file="$SECRET_TMP/webhook-url" \
      --dry-run=client -o yaml | kubectl apply -f -
    shred -u "$SECRET_TMP/webhook-url"
    echo "  Discord events webhook Secret applied"
  else
    echo "  WARN: DISCORD_WEBHOOK_URL_EVENTS unset — user-event Discord notify will be a no-op"
  fi
  ```

  Note the `--from-file=PATH` form auto-takes the basename as the Secret data
  key. Filenames `webhook-url-critical`, `webhook-url-warning`, `webhook-url`
  must match exactly — the AlertmanagerConfig CR and the backend Deployment
  reference these literal key names.

- [ ] **Step 3: Bash syntax check.**

  ```bash
  bash -n scripts/deploy.sh
  ```

  Expected: no output (syntax OK).

- [ ] **Step 4: Dry-run a single Secret render.**

  ```bash
  SECRET_TMP=$(mktemp -d); chmod 700 "$SECRET_TMP"
  printf '%s' 'https://discord.test/api/webhooks/1/secret-xyz' > "$SECRET_TMP/webhook-url"
  chmod 600 "$SECRET_TMP/webhook-url"
  kubectl create secret generic discord-events --from-file="$SECRET_TMP/webhook-url" --dry-run=client -o yaml
  shred -u "$SECRET_TMP/webhook-url"; rmdir "$SECRET_TMP"
  ```

  Expected: rendered Secret with `data.webhook-url: aHR0c[...]` (base64 of the URL); kubectl exits 0. If this fails because kubectl is not installed locally, skip this step and rely on Step 3.

- [ ] **Step 5: Commit.**

  ```bash
  git add scripts/deploy.sh
  git commit -m "$(cat <<'EOF'
  fix(scripts): stage Discord webhook Secrets via --from-file + shred [M-deploy-from-literal]

  kubectl --from-literal puts the secret value into argv, where it is readable
  from /proc/<pid>/cmdline. Switch to a mktemp -d staging dir with chmod 600
  per file + shred -u on cleanup. EXIT trap handles abort paths; explicit
  shred lines run on the happy path. Pattern matches scripts/recover-harbor.sh.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 5: [H-19] Use `git -c credential.helper` for the clone PAT

**Findings:** H-19 (HIGH). Recommended model: **sonnet**.

**Files:**

- Modify: `backend/app/services/build.py:178-211`
- Modify (add test): `backend/tests/test_services_build.py`

**Rationale:** The current clone initContainer assembles the URL inline:

```python
'"https://$GIT_USER:$GIT_TOKEN@github.com/$REPO.git" '
```

After shell expansion the resolved command is `git clone https://<user>:<pat>@github.com/...`. Even though `$GIT_USER` and `$GIT_TOKEN` come from `secretKeyRef`, the resulting argv is visible in:

- `kubectl describe pod <build-pod>` → `Command: [..., 'git clone https://user:ghp_...@github.com/...', ...]`
- Container runtime audit logs (`crictl inspect`)
- Anyone with `pods/exec` who can read `/proc/1/cmdline`

Git's credential-helper protocol expects a script that echoes `username=<u>` + `password=<p>` on stdout; git reads them and never builds an authenticated URL. Wrapping in `git -c credential.helper='!f() { echo username=$GIT_USER; echo password=$GIT_TOKEN; }; f'` keeps the token in env (which `kubectl describe` shows as `<set to the key 'token' of secret 'build-git-cred-<id>'>` — i.e., the reference, not the value) and out of argv.

- [ ] **Step 1: Write the failing test.**

  Append to `backend/tests/test_services_build.py`:

  ```python
  def test_clone_init_container_uses_credential_helper_not_inline_url():
      """H-19: PAT must not appear in argv. Verify the clone command uses
      git's credential.helper protocol instead of the inline
      https://$GIT_USER:$GIT_TOKEN@github.com/... pattern.
      """
      job = build_job_spec(
          build_id=uuid4(),
          detector_name="upxelfdet",
          git_tag="v0.1.0",
          owner_repo="bolin8017/upxelfdet",
      )
      spec = job["spec"]["template"]["spec"]
      clone = next(c for c in spec["initContainers"] if c["name"] == "clone")
      args = " ".join(clone["args"])

      # Inline-PAT URL pattern is forbidden.
      assert "$GIT_USER:$GIT_TOKEN@github.com" not in args, (
          "credential-bearing URL must be replaced by credential.helper [H-19]"
      )
      # credential.helper pattern is required.
      assert "credential.helper=" in args
      assert "echo username=$GIT_USER" in args
      assert "echo password=$GIT_TOKEN" in args
      # The clone URL is plain (no embedded creds).
      assert "https://github.com/$REPO.git" in args

      # Env still carries GIT_USER and GIT_TOKEN as secretKeyRef (not value).
      env_by_name = {e["name"]: e for e in clone["env"]}
      assert env_by_name["GIT_USER"]["valueFrom"]["secretKeyRef"]["key"] == "username"
      assert env_by_name["GIT_TOKEN"]["valueFrom"]["secretKeyRef"]["key"] == "token"
  ```

- [ ] **Step 2: Run the failing test.**

  ```bash
  cd backend && uv run pytest tests/test_services_build.py::test_clone_init_container_uses_credential_helper_not_inline_url -v
  ```

  Expected: FAIL — `"$GIT_USER:$GIT_TOKEN@github.com" not in args` fails on the current inline-URL form.

- [ ] **Step 3: Replace the clone args.**

  In `backend/app/services/build.py`, locate the `"clone"` initContainer (`name`/`image`/`command`/`args`/...). Replace the `args` block (currently `args: ["set +x; git clone ... https://$GIT_USER:$GIT_TOKEN@github.com/$REPO.git ... && git -C ..."]`) with:

  ```python
                          "command": ["/bin/sh", "-c"],
                          "args": [
                              "set +x; "
                              # H-19: git PAT must NOT appear in argv. Use git's
                              # credential helper — the inline helper script
                              # reads $GIT_USER and $GIT_TOKEN from env (which
                              # are valueFrom: secretKeyRef, not visible in
                              # kubectl describe pod) and echoes them on
                              # stdout for git to consume. The clone URL no
                              # longer carries any user:pass component.
                              "git -c credential.helper='!f() { echo username=$GIT_USER; echo password=$GIT_TOKEN; }; f' "
                              "clone --depth=1 --recurse-submodules "
                              '--branch="$GIT_TAG" '
                              '"https://github.com/$REPO.git" '
                              "/workspace/src && "
                              "git -C /workspace/src rev-parse HEAD > /workspace/git-sha"
                          ],
  ```

  Leave everything else in the clone init container (`env`, `volumeMounts`,
  `securityContext`, `resources`) untouched.

- [ ] **Step 4: Run the tests.**

  ```bash
  cd backend && uv run pytest tests/test_services_build.py -v
  ```

  Expected: all pass, including the new credential-helper test. If any other
  test was asserting the legacy `$GIT_USER:$GIT_TOKEN@` URL shape, update it to
  match the new credential-helper form.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/app/services/build.py backend/tests/test_services_build.py
  git commit -m "$(cat <<'EOF'
  fix(backend): clone via git credential.helper instead of inline PAT URL [H-19]

  The clone initContainer embedded the PAT in the URL, putting it in argv
  (kubectl describe pod / /proc/<pid>/cmdline / crictl inspect). Switch to
  git's credential-helper protocol: an inline shell function reads
  $GIT_USER and $GIT_TOKEN from env (still valueFrom: secretKeyRef) and
  echoes username= / password= on stdin. The clone URL is now plain
  https://github.com/$REPO.git with no credential component.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 6: [H-17a] Generate per-session Fernet key in `conftest.py`

**Findings:** H-17 part-a (HIGH). Recommended model: **sonnet**.

**Files:**

- Modify: `backend/tests/conftest.py:1-10`
- Modify (add test): `backend/tests/test_config_validation.py`

**Rationale:** The conftest.py at HEAD pins `FERNET_KEY=ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=`. That value is public — it sits in the repo, is in the test pyramid for years' worth of CI runs, and is the first thing a developer copies when they need "a Fernet key that works." If an operator ever pastes it into a `.lolday-secrets.env`, every encrypted token in prod is decryptable by anyone with the source. Generate per-test-session via `Fernet.generate_key()`. The hard-fail boot validator that catches the legacy key in prod lands in T8 (same module_validator that also enforces the FERNET_KEYS rename); T6 is the test-side half.

_Note: this task still uses `FERNET_KEY` (singular) — the env-var rename happens in T8. T6 only changes the value of the existing env, not its name._

- [ ] **Step 1: Write the failing regression test.**

  Append to `backend/tests/test_config_validation.py`:

  ```python
  def test_test_session_does_not_use_legacy_fernet_key():
      """H-17a: the conftest.py default for FERNET_KEY must NOT be the public
      test value that was hardcoded in the repo through 2026-05-13. Production
      defense lives in Settings.validate_fernet_keys (T8); this guard catches
      a future contributor who reverts conftest to a stable cleartext.
      """
      from app.config import settings

      LEGACY = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
      # Pre-T8 the field is FERNET_KEY (singular); post-T8 it is FERNET_KEYS (list).
      key_value = getattr(settings, "FERNET_KEY", None) or " ".join(
          getattr(settings, "FERNET_KEYS", []) or []
      )
      assert LEGACY not in key_value, (
          "Test session must use Fernet.generate_key() — legacy hardcoded value found"
      )
  ```

  The `getattr` shape lets the same test survive across T6 (pre-rename, singular field exists) and T8 (post-rename, plural field exists).

- [ ] **Step 2: Run the failing test.**

  ```bash
  cd backend && uv run pytest tests/test_config_validation.py::test_test_session_does_not_use_legacy_fernet_key -v
  ```

  Expected: FAIL — `settings.FERNET_KEY` is currently the legacy value.

- [ ] **Step 3: Update `backend/tests/conftest.py` lines 1-10.**

  Replace:

  ```python
  import os

  os.environ.setdefault("FERNET_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=")
  ```

  with:

  ```python
  import os

  from cryptography.fernet import Fernet

  # H-17a: never reuse a hardcoded Fernet key in tests — the value would be
  # public via git, and any operator who copies it into .lolday-secrets.env
  # makes encrypted columns trivially decryptable. Per-session fresh key.
  os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
  ```

  Keep all subsequent `os.environ.setdefault(...)` lines (`RECONCILER_ENABLED`,
  `FIFO_RECONCILER_ENABLED`, `SAMPLES_LOCAL_ROOT`, `ENVIRONMENT`) and the rest
  of the file unchanged.

- [ ] **Step 4: Run the test.**

  ```bash
  cd backend && uv run pytest tests/test_config_validation.py::test_test_session_does_not_use_legacy_fernet_key -v
  ```

  Expected: PASS.

- [ ] **Step 5: Run the full test suite to confirm no regressions.**

  ```bash
  cd backend && uv run pytest -q
  ```

  Expected: green. The crypto round-trip tests in `test_services_crypto.py`
  already use `TokenCipher.generate_key()` per-test so the conftest change
  is transparent. The git-credential set / get tests round-trip through
  `TokenCipher(settings.FERNET_KEY)`, which now sees a fresh per-session
  key — still a single key, still works.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/tests/conftest.py backend/tests/test_config_validation.py
  git commit -m "$(cat <<'EOF'
  fix(backend): generate per-session Fernet key in conftest.py [H-17a]

  The hardcoded ZmDfcTF7_60GrrY... key was public in the repo; copying it
  into .lolday-secrets.env makes every encrypted_token cleartext-equivalent
  for anyone with the source. Switch conftest to Fernet.generate_key() so
  tests get a fresh per-session value. Regression guard in
  test_config_validation.py. Production hard-fail lands in T8.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 7: [H-18] `TokenCipher` accepts a key iterable via `MultiFernet`

**Findings:** H-18 (HIGH). Recommended model: **opus** (cipher design).

**Files:**

- Modify: `backend/app/services/crypto.py`
- Modify (add tests): `backend/tests/test_services_crypto.py`

**Rationale:** `cryptography.fernet.MultiFernet` is the upstream-supported primitive for key rotation: construct with `[Fernet(new), Fernet(old)]`, encrypts under the first key, decrypts with whichever matches. Today `TokenCipher` is hard-wired to a single key — there's no way to deploy NEW alongside OLD without writing every row twice in lockstep. After this task `TokenCipher(keys)` accepts either a single key (current call shape — preserved for backward-compat) or an iterable of keys (rotation window). The Settings rename + chart wiring + caller updates happen in T8. The standalone `rotate_fernet.py` script lands in T9.

- [ ] **Step 1: Write the failing tests.**

  Append to `backend/tests/test_services_crypto.py`:

  ```python
  def test_multifernet_decrypts_with_either_key_in_list():
      """A token encrypted with k1 must decrypt under MultiFernet([k2, k1])
      (rotation window) and FAIL under MultiFernet([k2]) (rotation complete)."""
      k_old = TokenCipher.generate_key()
      k_new = TokenCipher.generate_key()
      ciphertext = TokenCipher(k_old).encrypt("hello")

      # Rotation window: decrypt with [new, old] succeeds.
      assert TokenCipher([k_new, k_old]).decrypt(ciphertext) == "hello"

      # Rotation complete: decrypt with [new] only fails.
      with pytest.raises(InvalidToken):
          TokenCipher([k_new]).decrypt(ciphertext)


  def test_multifernet_encrypts_with_first_key():
      """The leading key in the list is the active encrypt key; ciphertext is
      decryptable by that key alone."""
      k1 = TokenCipher.generate_key()
      k2 = TokenCipher.generate_key()
      ciphertext = TokenCipher([k1, k2]).encrypt("hello")

      # k1 alone decrypts (it was the encrypt key).
      assert TokenCipher(k1).decrypt(ciphertext) == "hello"
      # k2 alone cannot decrypt — it was only in the trial set for *future*
      # rotations, never used for encrypt.
      with pytest.raises(InvalidToken):
          TokenCipher(k2).decrypt(ciphertext)


  def test_empty_keys_iterable_raises_value_error():
      """Pydantic / chart sometimes hand us an empty list (misconfigured deploy).
      The constructor must fail loud, not silently fall through to a Fernet
      crash deep inside encrypt()."""
      with pytest.raises(ValueError, match="at least one"):
          TokenCipher([])


  def test_single_key_construction_still_supported():
      """Existing call sites pass a single str/bytes key — backward-compat."""
      key = TokenCipher.generate_key()
      ciphertext = TokenCipher(key).encrypt("hello")
      assert TokenCipher(key).decrypt(ciphertext) == "hello"

      # Also accepts str-key.
      key_str = key.decode()
      assert TokenCipher(key_str).encrypt("x") != b""
  ```

- [ ] **Step 2: Run the failing tests.**

  ```bash
  cd backend && uv run pytest tests/test_services_crypto.py -v
  ```

  Expected: the four new tests FAIL — `TokenCipher([k_new, k_old])` raises
  `AttributeError: 'list' object has no attribute 'encode'` because the
  current constructor calls `.encode()` directly on the input.

- [ ] **Step 3: Replace `backend/app/services/crypto.py`.**

  ```python
  """User-PAT symmetric encryption (Fernet / MultiFernet).

  Wraps cryptography's `MultiFernet` for key rotation. Construct with a single
  key (str/bytes) for the common single-key case, or with an iterable of keys
  to enable rotation: the FIRST key is used for encrypt; all keys are tried
  for decrypt. The operator deploys a new key in front of the old in
  ``FERNET_KEYS``, runs ``python -m app.scripts.rotate_fernet --old K_old
  --new K_new`` to re-encrypt every row, then retires the old key in a
  follow-up upgrade.
  """

  from collections.abc import Iterable
  from typing import Union

  from cryptography.fernet import Fernet, MultiFernet


  class TokenCipher:
      """Symmetric cipher for storing PATs encrypted at rest."""

      def __init__(
          self,
          keys: "Union[str, bytes, Iterable[Union[str, bytes]]]",
      ) -> None:
          # Single str/bytes wraps to a one-element list. Anything else is
          # treated as an iterable of keys.
          if isinstance(keys, (str, bytes)):
              key_list: list[Union[str, bytes]] = [keys]
          else:
              key_list = list(keys)
          if not key_list:
              raise ValueError("TokenCipher requires at least one Fernet key")
          fernets = [
              Fernet(k.encode() if isinstance(k, str) else k) for k in key_list
          ]
          self._fernet: "Fernet | MultiFernet" = (
              fernets[0] if len(fernets) == 1 else MultiFernet(fernets)
          )

      @staticmethod
      def generate_key() -> bytes:
          return Fernet.generate_key()

      def encrypt(self, plaintext: str) -> bytes:
          return self._fernet.encrypt(plaintext.encode())

      def decrypt(self, token: bytes) -> str:
          return self._fernet.decrypt(token).decode()

      @staticmethod
      def token_hint(token: str) -> str:
          """Human-readable hint that does not reveal the full token."""
          if len(token) <= 2:
              return token
          if len(token) <= 8:
              return f"{token[:2]}...{token[-2:]}"
          return f"{token[:4]}...{token[-4:]}"
  ```

  Note: the `Union[...]` quoting in the annotation avoids a mypy / runtime
  edge case with `from __future__ import annotations` not being set on this
  file historically. If pre-commit suggests `X | Y` form, use that — both work
  at runtime.

- [ ] **Step 4: Run the tests.**

  ```bash
  cd backend && uv run pytest tests/test_services_crypto.py -v
  ```

  Expected: all six tests pass (two pre-existing + four new).

- [ ] **Step 5: Run the full suite for regressions.**

  ```bash
  cd backend && uv run pytest -q
  ```

  Expected: green. Call sites still pass a single key (str or bytes) — the
  one-key path is unchanged.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/app/services/crypto.py backend/tests/test_services_crypto.py
  git commit -m "$(cat <<'EOF'
  fix(backend): TokenCipher accepts key iterable via MultiFernet [H-18]

  cryptography.MultiFernet is the upstream-supported primitive for key
  rotation: construct with [Fernet(new), Fernet(old)], encrypts under the
  first key, decrypts with whichever matches. TokenCipher now accepts
  either a single key (existing single-key call sites unchanged) or an
  iterable of keys. The Settings-level FERNET_KEY → FERNET_KEYS rename +
  chart wiring + caller updates land in T8; the standalone rotate_fernet.py
  script lands in T9.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 8: [H-17b + H-18b] Rename `FERNET_KEY` → `FERNET_KEYS`; hard-fail on legacy key

**Findings:** H-17 part-b + H-18 part-b (HIGH). Recommended model: **opus** (multi-file breaking change).

**Files:**

- Modify: `backend/app/config.py:1-20`
- Modify: `backend/app/routers/credentials.py:15-18`
- Modify: `backend/app/routers/detectors.py:71`
- Modify: `charts/lolday/templates/backend-fernet-secret.yaml`
- Modify: `charts/lolday/templates/backend.yaml:63-68`
- Modify: `charts/lolday/templates/alembic-upgrade-hook.yaml:62-68`
- Modify: `charts/lolday/values.yaml:40-42`
- Modify: `scripts/deploy.sh:11-30, 213-227`
- Modify: `.lolday-secrets.env.example`
- Modify: `backend/tests/conftest.py:8` (env var name)
- Modify (add tests): `backend/tests/test_config_validation.py`

**Rationale:** This is the breaking-change atomic commit (per user decision: hard-fail boot, no graceful fallback). Touches Settings field, chart Secret + Deployment env, alembic-hook env, values.yaml, deploy.sh `--set` flag + env-var requirement, secrets template, and conftest's env-var name. The `validate_fernet_keys` model_validator is the production hard-fail: in `ENVIRONMENT=production`, raise on empty `FERNET_KEYS` and on any inclusion of the public legacy test key. Settings exposes `FERNET_KEYS: list[str]` (parsed from the whitespace-separated env via a `field_validator`); callers pass the list to `TokenCipher` directly.

_Breaking change for operator:_ before this PR is deployed, the operator must rename `FERNET_KEY=$X` to `FERNET_KEYS=$X` in `.lolday-secrets.env`. The PR body calls this out.

- [ ] **Step 1: Write the failing tests.**

  Append to `backend/tests/test_config_validation.py`:

  ```python
  def _prod_env(monkeypatch):
      """Helper: fill in the rest of the production env so validate_sso_config
      and validate_helper_images don't pre-empt validate_fernet_keys."""
      monkeypatch.setenv("ENVIRONMENT", "production")
      monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "bolin8017.cloudflareaccess.com")
      monkeypatch.setenv("CF_ACCESS_APP_AUD", "x" * 64)
      monkeypatch.setenv(
          "BUILD_IMAGE_HELPER", "harbor.lolday.svc:80/lolday/build-helper:abc"
      )
      monkeypatch.setenv("JOB_HELPER_IMAGE", "harbor.lolday.svc:80/lolday/job-helper:def")


  def test_settings_rejects_legacy_fernet_key_in_production(monkeypatch):
      """H-17b: production must refuse the well-known test key."""
      _prod_env(monkeypatch)
      monkeypatch.setenv(
          "FERNET_KEYS", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
      )

      from app.config import Settings

      with pytest.raises(ValidationError, match="public test key"):
          Settings()


  def test_settings_rejects_legacy_fernet_key_anywhere_in_keys_list(monkeypatch):
      """Even when paired with a fresh key, the legacy value MUST be flagged —
      a half-rotated setup is still trivially decryptable for any row encrypted
      under the legacy key."""
      _prod_env(monkeypatch)
      fresh = Fernet.generate_key().decode()
      monkeypatch.setenv(
          "FERNET_KEYS",
          f"{fresh} ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=",
      )

      from app.config import Settings

      with pytest.raises(ValidationError, match="public test key"):
          Settings()


  def test_settings_rejects_empty_fernet_keys_in_production(monkeypatch):
      """H-18b: must have at least one key in production."""
      _prod_env(monkeypatch)
      monkeypatch.setenv("FERNET_KEYS", "")

      from app.config import Settings

      with pytest.raises(ValidationError, match="FERNET_KEYS is required"):
          Settings()


  def test_settings_parses_whitespace_separated_fernet_keys(monkeypatch):
      """Multiple keys whitespace-separated → list[str] with original order
      preserved (first key = active encrypt key, MultiFernet semantics)."""
      k1 = Fernet.generate_key().decode()
      k2 = Fernet.generate_key().decode()
      _prod_env(monkeypatch)
      monkeypatch.setenv("FERNET_KEYS", f"{k1}   {k2}")  # multiple spaces

      from app.config import Settings

      s = Settings()
      assert s.FERNET_KEYS == [k1, k2]


  def test_settings_singular_fernet_key_env_is_ignored_no_back_compat(monkeypatch):
      """Hard-fail rename: setting only FERNET_KEY (singular) must NOT populate
      FERNET_KEYS via fallback. Operator must rename in .lolday-secrets.env."""
      _prod_env(monkeypatch)
      monkeypatch.delenv("FERNET_KEYS", raising=False)
      monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())

      from app.config import Settings

      with pytest.raises(ValidationError, match="FERNET_KEYS is required"):
          Settings()
  ```

  Also add at the top of the file (with the other `from` imports):

  ```python
  from cryptography.fernet import Fernet
  ```

- [ ] **Step 2: Run the failing tests.**

  ```bash
  cd backend && uv run pytest tests/test_config_validation.py -v -k fernet
  ```

  Expected: all five new tests FAIL — `Settings` still has `FERNET_KEY: str = ""`,
  no `validate_fernet_keys` validator, no field_validator parsing whitespace.

- [ ] **Step 3: Update `backend/app/config.py`.**

  Replace the top of the file (`from pydantic import model_validator` through
  the `FERNET_KEY: str = ""` line) and the validator section at the bottom:

  ```python
  from pydantic import field_validator, model_validator
  from pydantic_settings import BaseSettings

  # The public Fernet key that was committed to backend/tests/conftest.py
  # through 2026-05-12. Anyone with read access to the repo possesses it; a
  # production deploy that inherits it makes every encrypted_token
  # cleartext-equivalent to a source-reading attacker.
  _LEGACY_TEST_FERNET_KEY = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="


  class Settings(BaseSettings):
      DATABASE_URL: str = "postgresql+asyncpg://lolday:password@postgresql:5432/lolday"
      REDIS_URL: str = "redis://redis:6379/0"
      DOCS_ENABLED: bool = True

      # ... keep BACKEND_MAINTENANCE_MODE through (and including) the comment block
      # immediately above the old FERNET_KEY line, then replace the FERNET_KEY
      # line itself with:

      # P3 (2026-05-13, H-18): whitespace-separated list of base64 Fernet keys.
      # First key is active for encrypt; all keys are tried for decrypt.
      # Operator rotates by adding the new key in front, running
      # ``python -m app.scripts.rotate_fernet --old <OLD> --new <NEW>``, then
      # dropping the old key after the run completes.
      FERNET_KEYS: list[str] = []
  ```

  (Keep everything else — HARBOR_URL through ENVIRONMENT — exactly as today.)

  Add the field validator immediately above the `validate_sso_config`
  model_validator:

  ```python
      @field_validator("FERNET_KEYS", mode="before")
      @classmethod
      def _split_fernet_keys(cls, v):
          """Accept whitespace-separated env value; collapse to list[str]."""
          if isinstance(v, str):
              return [k for k in v.split() if k]
          return v
  ```

  Add the model_validator after `validate_helper_images`:

  ```python
      @model_validator(mode="after")
      def validate_fernet_keys(self) -> "Settings":
          """Production refuses an empty list and refuses the public test key.

          Tests / dev bypass via ``ENVIRONMENT != "production"``. The split env
          parsing happens in ``_split_fernet_keys``; this validator only checks
          the resulting list.
          """
          if self.ENVIRONMENT != "production":
              return self
          if not self.FERNET_KEYS:
              raise ValueError(
                  "FERNET_KEYS is required in production (whitespace-separated "
                  "list of base64 Fernet keys; first key is active for encrypt). "
                  "FERNET_KEY (singular) was renamed in P3 — update "
                  ".lolday-secrets.env."
              )
          if _LEGACY_TEST_FERNET_KEY in self.FERNET_KEYS:
              raise ValueError(
                  "FERNET_KEYS contains the public test key from "
                  "backend/tests/conftest.py (committed to the repo until "
                  "2026-05-12) — encrypted columns would not actually be "
                  "secret. Generate a fresh key: "
                  "python -c \"from cryptography.fernet import Fernet; "
                  "print(Fernet.generate_key().decode())\""
              )
          return self
  ```

- [ ] **Step 4: Update callers.**

  In `backend/app/routers/credentials.py`, replace lines 15-18:

  ```python
  def _cipher() -> TokenCipher:
      if not settings.FERNET_KEYS:
          raise HTTPException(status_code=500, detail="FERNET_KEYS not configured")
      return TokenCipher(settings.FERNET_KEYS)
  ```

  In `backend/app/routers/detectors.py:71`:

  ```python
      return TokenCipher(settings.FERNET_KEYS).decrypt(cred.encrypted_token)
  ```

- [ ] **Step 5: Update the chart Secret + Deployment env.**

  Replace `charts/lolday/templates/backend-fernet-secret.yaml`:

  ```yaml
  {{- if .Values.backend.enabled }}
  apiVersion: v1
  kind: Secret
  metadata:
    name: backend-fernet-key
    namespace: {{ .Values.global.namespace }}
    labels:
      {{- include "lolday.labels" . | nindent 4 }}
  type: Opaque
  stringData:
    # P3 (H-18b): whitespace-separated list of base64 Fernet keys. The Secret
    # data key name stays ``key`` (singular) to keep the resource name
    # ``backend-fernet-key`` consistent — only the contained value type changes.
    key: {{ required "backend.fernetKeys is required (whitespace-separated list of base64 Fernet keys)" .Values.backend.fernetKeys | quote }}
  {{- end }}
  ```

  In `charts/lolday/templates/backend.yaml`, update the env binding (lines 63-68):

  ```yaml
  env:
    - name: FERNET_KEYS
      valueFrom:
        secretKeyRef:
          name: backend-fernet-key
          key: key
  ```

  In `charts/lolday/values.yaml` around line 40-42, rename:

  ```yaml
  backend:
    # ... preserve everything above and below ...
    fernetKeys: "" # whitespace-separated list of base64 Fernet keys; --set at deploy time
  ```

  In `charts/lolday/templates/alembic-upgrade-hook.yaml:62-68`, update the
  comment block to reference the new field name:

  ```yaml
  # Alembic only needs DATABASE_URL. `app.config.Settings` uses a
  # default `FERNET_KEYS: list[str] = []`, so importing `app.models`
  # does NOT require the Fernet secret — the earlier env binding
  # was a mis-attribution and also caused a fresh-install crash
  # because `backend-fernet-key` (a normal template) doesn't exist
  # when the pre-install hook fires.
  ```

- [ ] **Step 6: Update `scripts/deploy.sh`.**

  Near the top (around line 11-30 where `: "${PG_PASSWORD:?...}"` patterns are
  declared), find the `FERNET_KEY` env-var requirement (if present — check
  current file) and rename:

  ```bash
  : "${FERNET_KEYS:?FERNET_KEYS must be set (whitespace-separated list; first key is active for encrypt) — generate one via: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"}"
  ```

  In the `helm upgrade --install` block (line 213-227), change:

  ```bash
    --set backend.fernetKey="$FERNET_KEY" \
  ```

  to:

  ```bash
    --set backend.fernetKeys="$FERNET_KEYS" \
  ```

- [ ] **Step 7: Update `.lolday-secrets.env.example`.**

  Find the `FERNET_KEY=` block (with the multi-line comment) and replace with:

  ```
  # Phase 3 / P3 — backend Fernet keys for encrypted columns. Whitespace-
  # separated list of base64 keys; the FIRST key is active for encrypt, all
  # keys are tried for decrypt. Rotate via app.scripts.rotate_fernet — see
  # docs/runbooks/p3-fernet-rotation.md.
  # Generate a single key with:
  #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  # Renaming note: this was FERNET_KEY (singular) before P3. If you have a
  # legacy single key, just set FERNET_KEYS=$YOUR_OLD_KEY — same value, plural
  # variable name. No data migration required for deploy; rotation is a
  # separate operator action.
  FERNET_KEYS=
  ```

- [ ] **Step 8: Update `backend/tests/conftest.py`.**

  Change the line set in T6 from:

  ```python
  os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
  ```

  to:

  ```python
  os.environ.setdefault("FERNET_KEYS", Fernet.generate_key().decode())
  ```

  The Fernet import already exists from T6.

- [ ] **Step 9: Run the failing tests.**

  ```bash
  cd backend && uv run pytest tests/test_config_validation.py -v -k fernet
  ```

  Expected: all five new tests PASS.

- [ ] **Step 10: Run the full suite.**

  ```bash
  cd backend && uv run pytest -q
  ```

  Expected: green. Pydantic fixture cascades (per the engineering hygiene note
  in P1) may flag a few existing tests that still reference `settings.FERNET_KEY`
  — `grep -rn "FERNET_KEY\b" backend/` to find them, rename to `FERNET_KEYS`,
  re-run. Any test that built a TokenCipher off `settings.FERNET_KEY` should now
  pass `settings.FERNET_KEYS` (list of one) — TokenCipher accepts both shapes
  after T7, so the change is mechanical.

- [ ] **Step 11: helm lint with the renamed flag.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test \
    --set backend.fernetKeys=test \
    --set postgresql.auth.password=test \
    --set mlflow.auth.password=test --set mlflow.db.password=test \
    --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`. **From this task onward,
  every helm-lint step uses `--set backend.fernetKeys=test` instead of the
  pre-P3 `--set fernetKey=test`.**

- [ ] **Step 12: Commit.**

  ```bash
  git add backend/app/config.py backend/app/routers/credentials.py \
    backend/app/routers/detectors.py backend/tests/conftest.py \
    backend/tests/test_config_validation.py \
    charts/lolday/templates/backend-fernet-secret.yaml \
    charts/lolday/templates/backend.yaml \
    charts/lolday/templates/alembic-upgrade-hook.yaml \
    charts/lolday/values.yaml scripts/deploy.sh .lolday-secrets.env.example
  git commit -m "$(cat <<'EOF'
  fix(backend)!: rename FERNET_KEY to FERNET_KEYS and hard-fail on legacy key [H-17b][H-18b]

  Breaking change: the singular FERNET_KEY env / chart value / deploy.sh
  flag is renamed to plural FERNET_KEYS (whitespace-separated list of
  base64 Fernet keys; first key is active for encrypt). Operator MUST
  rename the value in .lolday-secrets.env before the next deploy. The
  TokenCipher primitive (T7) already handles both single-key and list
  shapes, so a same-value rename ships with no data migration; rotation
  is an independent operator action enabled by app.scripts.rotate_fernet
  (T9) + docs/runbooks/p3-fernet-rotation.md (T10).

  Settings.validate_fernet_keys raises ValidationError in production when
  FERNET_KEYS is empty or contains the public test key
  ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg= committed in conftest.py
  through 2026-05-12.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 9: [H-18a] `rotate_fernet.py` re-encryption script

**Findings:** H-18a (HIGH). Recommended model: **opus** (transactional script design).

**Files:**

- Create: `backend/app/scripts/__init__.py` (empty package marker)
- Create: `backend/app/scripts/rotate_fernet.py`
- Create: `backend/tests/test_scripts_rotate_fernet.py`

**Rationale:** `MultiFernet` decrypts under any key in the list and encrypts under the first. For the operator to retire the OLD key after deploying NEW, every row of `user_git_credential.encrypted_token` must be re-encrypted under NEW. The script:

- Takes `--old` and `--new` as explicit CLI args (NOT env — avoids interaction with the live `FERNET_KEYS` env on the running backend, which is whatever the operator deployed).
- Iterates `UserGitCredential` rows; for each row that decrypts under NEW already, skip (idempotent — safe to re-run after partial completion); for each row that decrypts under OLD but not NEW, re-encrypt with NEW and commit. One row per transaction (PostgreSQL gives autonomous-transaction semantics via per-row `commit()`).
- Aborts on the first row that decrypts under neither key — leaves committed rows in NEW state, uncommitted rows in OLD state. Both decryptable under `MultiFernet([NEW, OLD])` on the running backend.

- [ ] **Step 1: Create the package marker.**

  ```bash
  mkdir -p backend/app/scripts
  : > backend/app/scripts/__init__.py
  ```

- [ ] **Step 2: Write the failing tests.**

  Create `backend/tests/test_scripts_rotate_fernet.py`:

  ```python
  """Tests for backend/app/scripts/rotate_fernet.py."""

  import pytest
  from cryptography.fernet import Fernet, InvalidToken
  from sqlalchemy import select


  @pytest.mark.asyncio
  async def test_rotate_reencrypts_rows_under_new_key(db_session, monkeypatch):
      """Insert a row encrypted under k1; rotate(k1, k2); row must now decrypt
      under k2 alone and fail under k1 alone."""
      from app.models import Role, User
      from app.models.credential import GitProvider, UserGitCredential
      from app.scripts import rotate_fernet
      from app.services.crypto import TokenCipher

      k1 = Fernet.generate_key().decode()
      k2 = Fernet.generate_key().decode()

      user = User(
          email="rotate-1@x.com",
          handle="rotate-1",
          role=Role.USER,
          display_name="rotate-1",
      )
      db_session.add(user)
      await db_session.flush()
      plaintext = "ghp_a" * 8
      db_session.add(
          UserGitCredential(
              user_id=user.id,
              provider=GitProvider.GITHUB,
              encrypted_token=TokenCipher(k1).encrypt(plaintext),
              token_hint=TokenCipher.token_hint(plaintext),
          )
      )
      await db_session.commit()

      # Point rotate_fernet at the test sqlite session_maker.
      from backend.tests.conftest import test_session_maker  # noqa: PLC0415  # cross-test reuse
      monkeypatch.setattr(rotate_fernet, "async_session_maker", test_session_maker)

      rotated, skipped = await rotate_fernet.rotate_all(k1, k2)
      assert rotated == 1
      assert skipped == 0

      row = (
          await db_session.execute(
              select(UserGitCredential).where(UserGitCredential.user_id == user.id)
          )
      ).scalar_one()
      # Decryptable under k2 alone.
      assert TokenCipher(k2).decrypt(row.encrypted_token) == plaintext
      # NOT decryptable under k1 alone.
      with pytest.raises(InvalidToken):
          TokenCipher(k1).decrypt(row.encrypted_token)


  @pytest.mark.asyncio
  async def test_rotate_is_idempotent_skips_already_rotated(
      db_session, monkeypatch
  ):
      """Running rotate(k1, k2) twice in a row leaves row state unchanged on
      the second run — already-decryptable-under-k2 rows are skipped."""
      from app.models import Role, User
      from app.models.credential import GitProvider, UserGitCredential
      from app.scripts import rotate_fernet
      from app.services.crypto import TokenCipher

      k1 = Fernet.generate_key().decode()
      k2 = Fernet.generate_key().decode()

      user = User(
          email="rotate-2@x.com",
          handle="rotate-2",
          role=Role.USER,
          display_name="rotate-2",
      )
      db_session.add(user)
      await db_session.flush()
      db_session.add(
          UserGitCredential(
              user_id=user.id,
              provider=GitProvider.GITHUB,
              encrypted_token=TokenCipher(k1).encrypt("hello"),
              token_hint="he...lo",
          )
      )
      await db_session.commit()

      from backend.tests.conftest import test_session_maker  # noqa: PLC0415
      monkeypatch.setattr(rotate_fernet, "async_session_maker", test_session_maker)

      rotated1, skipped1 = await rotate_fernet.rotate_all(k1, k2)
      rotated2, skipped2 = await rotate_fernet.rotate_all(k1, k2)
      assert (rotated1, skipped1) == (1, 0)
      assert (rotated2, skipped2) == (0, 1)


  @pytest.mark.asyncio
  async def test_rotate_aborts_on_undecryptable_row(db_session, monkeypatch):
      """A row encrypted under a third unknown key triggers abort — committed
      rows stay rotated, the unrotatable row stays in its original (under-k3)
      state, exception propagates."""
      from app.models import Role, User
      from app.models.credential import GitProvider, UserGitCredential
      from app.scripts import rotate_fernet
      from app.services.crypto import TokenCipher

      k1 = Fernet.generate_key().decode()
      k2 = Fernet.generate_key().decode()
      k3_unknown = Fernet.generate_key().decode()

      # Row A: encrypted under k1 — rotatable.
      user_a = User(
          email="rotate-3a@x.com",
          handle="rotate-3a",
          role=Role.USER,
          display_name="rotate-3a",
      )
      # Row B: encrypted under k3 — UNROTATABLE.
      user_b = User(
          email="rotate-3b@x.com",
          handle="rotate-3b",
          role=Role.USER,
          display_name="rotate-3b",
      )
      db_session.add_all([user_a, user_b])
      await db_session.flush()
      db_session.add_all(
          [
              UserGitCredential(
                  user_id=user_a.id,
                  provider=GitProvider.GITHUB,
                  encrypted_token=TokenCipher(k1).encrypt("a"),
                  token_hint="a",
              ),
              UserGitCredential(
                  user_id=user_b.id,
                  provider=GitProvider.GITHUB,
                  encrypted_token=TokenCipher(k3_unknown).encrypt("b"),
                  token_hint="b",
              ),
          ]
      )
      await db_session.commit()

      from backend.tests.conftest import test_session_maker  # noqa: PLC0415
      monkeypatch.setattr(rotate_fernet, "async_session_maker", test_session_maker)

      with pytest.raises(RuntimeError, match="unrotatable row"):
          await rotate_fernet.rotate_all(k1, k2)
  ```

- [ ] **Step 3: Run the failing tests.**

  ```bash
  cd backend && uv run pytest tests/test_scripts_rotate_fernet.py -v
  ```

  Expected: all three FAIL — `app.scripts.rotate_fernet` does not exist.

- [ ] **Step 4: Create `backend/app/scripts/rotate_fernet.py`.**

  ```python
  """Re-encrypt UserGitCredential.encrypted_token from OLD Fernet key to NEW.

  Usage::

      cd backend
      uv run python -m app.scripts.rotate_fernet --old "$OLD_KEY" --new "$NEW_KEY"

  Idempotent: rows already decryptable under NEW alone are skipped. Aborts on
  the first row that decrypts under neither key, leaving already-rotated rows
  intact for inspection / re-run. Run BEFORE retiring the OLD key from
  FERNET_KEYS. See docs/runbooks/p3-fernet-rotation.md for the full operator
  procedure.

  Why explicit --old / --new CLI args (not env): the running backend's
  FERNET_KEYS env reflects whatever was deployed; the rotation script needs
  to know which key was the previous active encrypt key and which is the new
  one, independent of the deployment state at run time.
  """

  from __future__ import annotations

  import argparse
  import asyncio
  import logging
  import sys

  from cryptography.fernet import InvalidToken
  from sqlalchemy import select

  from app.db import async_session_maker
  from app.models.credential import UserGitCredential
  from app.services.crypto import TokenCipher

  logger = logging.getLogger(__name__)


  async def rotate_all(old_key: str, new_key: str) -> tuple[int, int]:
      """Re-encrypt every UserGitCredential row. Returns (rotated, skipped).

      Per-row commit is the autonomous-transaction primitive: under PostgreSQL,
      each ``await session.commit()`` is its own transaction. Aborting after a
      partial run leaves committed rows in the NEW-key state and unprocessed
      rows in the OLD-key state — both are decryptable on the running backend
      under ``MultiFernet([NEW, OLD])``.

      Raises ``RuntimeError`` on the first row that decrypts under neither key.
      """
      new_cipher = TokenCipher(new_key)
      old_cipher = TokenCipher(old_key)
      rotated, skipped = 0, 0
      async with async_session_maker() as session:
          rows = (await session.execute(select(UserGitCredential))).scalars().all()
          for row in rows:
              # Already-rotated? Skip silently — re-runs are safe.
              try:
                  new_cipher.decrypt(row.encrypted_token)
                  skipped += 1
                  continue
              except InvalidToken:
                  pass
              # Decrypt under OLD; abort if it fails.
              try:
                  plaintext = old_cipher.decrypt(row.encrypted_token)
              except InvalidToken as exc:
                  logger.error(
                      "rotate_fernet: row user_id=%s decrypts under neither old "
                      "nor new key; aborting (committed rows are already in "
                      "NEW state)",
                      row.user_id,
                  )
                  raise RuntimeError(
                      f"unrotatable row: user_id={row.user_id}"
                  ) from exc
              # Re-encrypt with NEW and commit (per-row autonomous tx).
              row.encrypted_token = new_cipher.encrypt(plaintext)
              await session.commit()
              rotated += 1
      return rotated, skipped


  def main() -> int:
      logging.basicConfig(
          level=logging.INFO,
          format="%(asctime)s %(levelname)s rotate_fernet: %(message)s",
      )
      parser = argparse.ArgumentParser(
          description=(
              "Re-encrypt UserGitCredential rows from OLD to NEW Fernet key. "
              "Run during the maintenance window described in "
              "docs/runbooks/p3-fernet-rotation.md."
          )
      )
      parser.add_argument(
          "--old", required=True, help="base64 Fernet key being retired"
      )
      parser.add_argument(
          "--new",
          required=True,
          help="base64 Fernet key already deployed as the active encrypt key",
      )
      args = parser.parse_args()
      try:
          rotated, skipped = asyncio.run(rotate_all(args.old, args.new))
      except RuntimeError as exc:
          logger.error("rotate_fernet aborted: %s", exc)
          return 2
      logger.info("done — rotated=%d skipped=%d", rotated, skipped)
      return 0


  if __name__ == "__main__":
      sys.exit(main())
  ```

- [ ] **Step 5: Run the tests.**

  ```bash
  cd backend && uv run pytest tests/test_scripts_rotate_fernet.py -v
  ```

  Expected: all three pass.

- [ ] **Step 6: Smoke-test the CLI entry point.**

  ```bash
  cd backend && uv run python -m app.scripts.rotate_fernet --help
  ```

  Expected: argparse help output describing `--old` and `--new`. No DB
  connection at this point (argparse runs before asyncio.run).

- [ ] **Step 7: Commit.**

  ```bash
  git add backend/app/scripts/__init__.py backend/app/scripts/rotate_fernet.py \
    backend/tests/test_scripts_rotate_fernet.py
  git commit -m "$(cat <<'EOF'
  feat(backend): rotate_fernet.py script for offline Fernet re-encryption [H-18a]

  python -m app.scripts.rotate_fernet --old <OLD> --new <NEW> iterates every
  UserGitCredential row, decrypts under OLD and re-encrypts under NEW, one
  row per autonomous transaction. Idempotent — rows already decryptable
  under NEW are skipped, so re-runs are safe. Aborts on the first row that
  decrypts under neither key, leaving committed rows in NEW state and
  uncommitted rows in OLD state (both readable on the running backend
  under MultiFernet([NEW, OLD])). The full operator procedure is in
  docs/runbooks/p3-fernet-rotation.md (T10).

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 10: Operator runbook for Fernet rotation

**Files:**

- Create: `docs/runbooks/p3-fernet-rotation.md`
- Modify: `docs/superpowers/specs/2026-05-12-security-hardening-design.md` (cross-link the runbook from §6.3)
- Modify: `CLAUDE.md` (add the runbook to the navigation index)

**Rationale:** T7–T9 ship the primitives; the runbook is the procedure that ties them together. Must read like a checklist an operator can follow under maintenance pressure.

- [ ] **Step 1: Create the runbook.**

  Create `docs/runbooks/p3-fernet-rotation.md`:

  ````markdown
  # Fernet key rotation — operator runbook (P3)

  **Scope:** rotating the backend's `FERNET_KEYS` symmetric key without losing
  the encrypted-at-rest data in `user_git_credential.encrypted_token`.

  **Cadence:** ad-hoc (after credential leak suspicion, key compromise, or
  periodic hygiene — recommended annually). Each rotation is one maintenance
  window of ≤30 min.

  **Why a script, not a hot rotation:** every encrypted column has to be
  re-encrypted under the new key. The `MultiFernet` primitive lets the running
  backend decrypt under either key during a 24 h window, but the offline
  re-encryption is one transaction per row and aborts cleanly on any
  unrotatable row.

  ## Pre-flight (T-10 min)

  - [ ] Confirm `helm upgrade --install lolday` is healthy: `kubectl -n lolday get pods`.
  - [ ] Confirm a recent Postgres backup exists (`kubectl -n lolday exec deploy/postgresql -- pg_dump ... > backup.sql`).
  - [ ] Confirm operator workstation has the current `FERNET_KEYS` value in `.lolday-secrets.env`.

  ## Procedure

  ```text
  # T-0 = maintenance window start

  # 1. Cordon backend submissions so no new tokens are written under the OLD
  #    key during the rotation window.
  helm upgrade lolday charts/lolday --reuse-values \
    --set backend.acceptingJobs=false
  #    (acceptingJobs is the existing BACKEND_MAINTENANCE_MODE feature flag —
  #     POST /api/v1/jobs and POST /api/v1/detectors/{id}/builds return 503.)

  # 2. Wait for in-flight builds and jobs to drain.
  kubectl -n lolday-jobs get vcjob,job
  # Expected: all reach Completed / Failed within ~20 min. If not, escalate.

  # 3. Export the OLD key.
  OLD=$(kubectl -n lolday get secret backend-fernet-key -o jsonpath='{.data.key}' | base64 -d)
  echo "OLD key length: ${#OLD}"  # sanity: 44 chars (single key)

  # 4. Generate the NEW key.
  NEW=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  echo "NEW key length: ${#NEW}"  # sanity: 44 chars

  # 5. Deploy NEW alongside OLD (NEW first → active for encrypt).
  helm upgrade lolday charts/lolday --reuse-values \
    --set backend.fernetKeys="$NEW $OLD"
  kubectl -n lolday rollout status deploy/backend  # wait for rollout

  # 6. Re-encrypt every UserGitCredential row under NEW.
  kubectl -n lolday exec deploy/backend -- \
    uv run --project /app python -m app.scripts.rotate_fernet \
      --old "$OLD" --new "$NEW"
  # Expected log: "done — rotated=<N> skipped=0"
  # On abort: the message names the user_id; inspect the row, decide whether to
  # delete or wait, then re-run. The script is idempotent.

  # 7. Uncordon — backend resumes accepting new submissions.
  helm upgrade lolday charts/lolday --reuse-values \
    --set backend.acceptingJobs=true

  # 8. Update .lolday-secrets.env on the operator workstation:
  #    FERNET_KEYS="$NEW $OLD"
  #    (so the next regular deploy reflects the same state).

  # ============ T+24h: retire the OLD key. ============

  # 9. Re-check: does any row still decrypt only under OLD? (Defensive — the
  #    script in step 6 should have caught these, but new writes between
  #    step 5 and step 7 went through MultiFernet([NEW, OLD]) and were
  #    encrypted under NEW. The 24 h cushion is for any out-of-band re-import
  #    that may have used the OLD-key path.)
  kubectl -n lolday exec deploy/backend -- \
    uv run --project /app python -m app.scripts.rotate_fernet --old "$OLD" --new "$NEW"
  # Expected: "rotated=0 skipped=<N>". If rotated > 0, do NOT proceed to step 10.

  # 10. Drop the OLD key.
  helm upgrade lolday charts/lolday --reuse-values \
    --set backend.fernetKeys="$NEW"
  kubectl -n lolday rollout status deploy/backend

  # 11. Update .lolday-secrets.env:
  #     FERNET_KEYS="$NEW"
  ```

  ## Rollback

  If step 6 (re-encryption) hits an unrotatable row and the operator can't
  immediately repair it:

  - The backend continues to run under `MultiFernet([NEW, OLD])` — both keys
    decrypt, so all reads succeed.
  - New writes go under NEW (first key).
  - Committed rows are in NEW state; uncommitted rows are still in OLD state.
  - Re-run step 6 after fixing the bad row (or deleting it). The script is
    idempotent.

  If step 9 (T+24h verification) shows `rotated > 0` (an out-of-band write
  re-encrypted under OLD between step 7 and step 9 — should not happen,
  defensive):

  - Re-run step 6 to roll those rows forward.
  - Re-run step 9 — if still `rotated == 0`, proceed to step 10.
  - If you cannot reach `rotated == 0`, do NOT drop OLD: investigate the
    code path that's still writing under the wrong cipher.
  ````

- [ ] **Step 2: Add the runbook to the spec cross-reference.**

  In `docs/superpowers/specs/2026-05-12-security-hardening-design.md` §6.3,
  immediately after the **Acceptance criteria:** block, append:

  ```markdown
  **Operator runbook:** [`docs/runbooks/p3-fernet-rotation.md`](../../runbooks/p3-fernet-rotation.md).
  ```

- [ ] **Step 3: Add the runbook to `CLAUDE.md` navigation.**

  In `CLAUDE.md` § "How to navigate this codebase", append (under the
  appropriate sub-bullet):

  ```markdown
  - Fernet key rotation (P3 operator runbook) → `docs/runbooks/p3-fernet-rotation.md`
  ```

- [ ] **Step 4: Lint the markdown.**

  ```bash
  pre-commit run --files docs/runbooks/p3-fernet-rotation.md
  ```

  Expected: clean (markdown hooks pass).

- [ ] **Step 5: Commit.**

  ```bash
  git add docs/runbooks/p3-fernet-rotation.md \
    docs/superpowers/specs/2026-05-12-security-hardening-design.md CLAUDE.md
  git commit -m "$(cat <<'EOF'
  docs(runbooks): operator procedure for Fernet key rotation [H-18a]

  Documents the 11-step rotation: cordon → drain → deploy NEW+OLD →
  re-encrypt → uncordon → 24 h cushion → re-verify → drop OLD. Pairs with
  app.scripts.rotate_fernet (T9). Cross-linked from the security
  spec §6.3 acceptance criteria block and from CLAUDE.md navigation.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 11: [M-token-secret-owner part-a] Patch `job-token-<id>` Secret with vcjob `ownerReferences`

**Findings:** M-token-secret-owner part-a (MEDIUM). Recommended model: **opus** (K8s GC contract).

**Files:**

- Modify: `backend/app/services/jobs_dispatch.py`
- Modify (conftest stub): `backend/tests/conftest.py` (`_StubVolcano.create_namespaced_custom_object` → inject `metadata.uid`; `_StubCore` → add `patch_namespaced_secret`)
- Create: `backend/tests/test_jobs_dispatch_owner_ref.py`

**Rationale:** Today `job-token-<id>` Secrets and their parent vcjobs live independently in `lolday-jobs` ns. If a vcjob gets deleted (via `kubectl delete vcjob` or via Volcano's TTL), the Secret stays — and it carries a valid bearer token until manually swept by the orphans reconciler. Setting `ownerReferences` on the Secret makes Kubernetes garbage-collect it whenever the parent vcjob is deleted (cascading delete). `blockOwnerDeletion: False` because the Secret must never block vcjob deletion (the GC-finalizer would deadlock if backend RBAC can't update Secrets in that ns). `controller: False` because vcjob is not a controller-of-Secret in the canonical sense (no reconcile contract); we use ownerReferences purely for the GC edge. The reconciler-sweep belt-and-suspenders lives in T12.

- [ ] **Step 1: Extend the conftest stubs.**

  In `backend/tests/conftest.py`, locate the `_StubVolcano.create_namespaced_custom_object` (inside `mock_k8s_batch`). Replace its body with:

  ```python
          def create_namespaced_custom_object(
              self, group, version, namespace, plural, body, **kw
          ):
              import uuid as _uu

              name = (
                  (body.get("metadata") or {}).get("name")
                  if isinstance(body, dict)
                  else body.metadata.name
              )
              # M-token-secret-owner: dispatch_job_to_volcano reads metadata.uid
              # from this response to populate Secret ownerReferences. Real K8s
              # always populates uid on create; mirror that here.
              if isinstance(body, dict):
                  body.setdefault("metadata", {}).setdefault("uid", str(_uu.uuid4()))
              self.objects[name] = body
              return body
  ```

  In the same conftest's `_StubCore` (inside `mock_k8s_batch`), add (next to
  the existing `create_namespaced_secret` / `delete_namespaced_secret` methods):

  ```python
          def __init__(self):
              self.secret_patches: list[tuple[str, str, dict]] = []

          def patch_namespaced_secret(self, name, namespace, body, **kw):
              # M-token-secret-owner: record ownerReferences patches for assertion.
              self.secret_patches.append((name, namespace, body))
              return body
  ```

  Update the `monkeypatch.setattr(f"{_mod}.{_name}", lambda: _StubCore())`
  helper to retain a per-test stub identity. Because the helper creates a new
  stub each access, tests that need to inspect `secret_patches` must monkeypatch
  the same lambda. Add a session-scoped accessor:

  ```python
  _shared_core_stub = None


  def _shared_core_v1():
      global _shared_core_stub
      if _shared_core_stub is None:
          _shared_core_stub = _StubCore()
      return _shared_core_stub
  ```

  Actually — to keep this surgical and avoid touching the existing
  loop-over-module-list pattern, store the stub on `monkeypatch` via a
  closure. Use the simpler form: tests that assert on `secret_patches` will
  patch `app.services.jobs_dispatch.core_v1` directly with their own stub
  factory inside the test, mirroring how `test_reconciler_orphans.py` already
  patches `app.reconciler.orphans.core_v1`. Skip the global-stub variant.

  Net effect on `conftest.py`: `_StubVolcano.create_namespaced_custom_object`
  injects `uid`; `_StubCore.__init__` initializes `secret_patches: list`;
  `_StubCore.patch_namespaced_secret` records calls. No other change to the
  registration loop.

- [ ] **Step 2: Write the failing test.**

  Create `backend/tests/test_jobs_dispatch_owner_ref.py`:

  ```python
  """M-token-secret-owner: dispatch_job_to_volcano patches the job-token Secret
  with ownerReferences pointing at the just-created vcjob, so K8s GC cascades
  the Secret deletion when the vcjob is removed."""

  import pytest
  from unittest.mock import patch
  from uuid import uuid4


  @pytest.mark.asyncio
  async def test_dispatch_patches_token_secret_with_vcjob_owner(
      db_session, seed_user, seed_detector_version
  ):
      from app.models.job import Job, JobStatus, JobType
      from app.services.jobs_dispatch import dispatch_job_to_volcano

      # Stub state we can inspect later.
      patches: list = []
      created_vcjob_uid = "vcjob-uid-deadbeef"

      class _Volcano:
          def create_namespaced_custom_object(
              self, group, version, namespace, plural, body, **kw
          ):
              body.setdefault("metadata", {}).setdefault("uid", created_vcjob_uid)
              return body

      class _Core:
          def create_namespaced_secret(self, namespace, body, **kw):
              return body

          def patch_namespaced_secret(self, name, namespace, body, **kw):
              patches.append((name, namespace, body))
              return body

      # ensure_user_queue is async; stub it to a literal queue name.
      async def _fake_queue(_owner_id):
              return "lolday-u-fake"

      dv_id = await seed_detector_version()
      job = Job(
          type=JobType.TRAIN,
          status=JobStatus.QUEUED_BACKEND,
          detector_version_id=dv_id,
          owner_id=seed_user.id,
          resolved_config={},
          idempotency_key=uuid4().hex,
      )
      db_session.add(job)
      await db_session.commit()
      await db_session.refresh(job)

      with (
          patch("app.services.jobs_dispatch.volcano_v1alpha1", return_value=_Volcano()),
          patch("app.services.jobs_dispatch.core_v1", return_value=_Core()),
          patch("app.services.jobs_dispatch.ensure_user_queue", _fake_queue),
      ):
          await dispatch_job_to_volcano(db_session, job)

      assert len(patches) == 1
      name, namespace, body = patches[0]
      assert name.startswith("job-token-")
      assert namespace  # lolday or lolday-jobs depending on settings
      owner_refs = body["metadata"]["ownerReferences"]
      assert len(owner_refs) == 1
      assert owner_refs[0]["kind"] == "Job"  # Volcano Job (not batch/v1)
      assert owner_refs[0]["apiVersion"].startswith("batch.volcano.sh/")
      assert owner_refs[0]["uid"] == created_vcjob_uid
      assert owner_refs[0]["blockOwnerDeletion"] is False
      assert owner_refs[0]["controller"] is False
  ```

- [ ] **Step 3: Run the failing test.**

  ```bash
  cd backend && uv run pytest tests/test_jobs_dispatch_owner_ref.py -v
  ```

  Expected: FAIL — no `patch_namespaced_secret` is called today.

- [ ] **Step 4: Implement the patch.**

  In `backend/app/services/jobs_dispatch.py`, replace the existing
  `try / except` block around `volcano_v1alpha1().create_namespaced_custom_object`
  with:

  ```python
      try:
          vcjob_resp = await asyncio.to_thread(
              volcano_v1alpha1().create_namespaced_custom_object,
              group=VOLCANO_BATCH_GROUP,
              version=VOLCANO_BATCH_VERSION,
              namespace=settings.JOB_NAMESPACE,
              plural=VOLCANO_JOB_PLURAL,
              body=manifest,
          )
      except Exception:
          # Roll back the token secret we just created so we leave no orphaned
          # secrets behind on a partial failure.
          with contextlib.suppress(Exception):
              await asyncio.to_thread(
                  core_v1().delete_namespaced_secret,
                  name=secret["metadata"]["name"],
                  namespace=settings.JOB_NAMESPACE,
              )
          raise

      # M-token-secret-owner: bind the Secret's lifetime to the vcjob via
      # metadata.ownerReferences so K8s GC removes it whenever the vcjob is
      # deleted. blockOwnerDeletion=False so a stuck Secret never blocks
      # vcjob deletion; controller=False because vcjob is not a reconcile
      # owner of Secret in the canonical sense — we use ownerReferences
      # only for the GC edge. The reconciler sweep in
      # ``app/reconciler/orphans.py`` is belt-and-suspenders for vcjobs
      # force-deleted with ``--grace-period=0`` (skips the GC step).
      vcjob_uid = (vcjob_resp.get("metadata") or {}).get("uid")
      if vcjob_uid:
          try:
              await asyncio.to_thread(
                  core_v1().patch_namespaced_secret,
                  name=secret["metadata"]["name"],
                  namespace=settings.JOB_NAMESPACE,
                  body={
                      "metadata": {
                          "ownerReferences": [
                              {
                                  "apiVersion": f"{VOLCANO_BATCH_GROUP}/{VOLCANO_BATCH_VERSION}",
                                  "kind": "Job",
                                  "name": manifest["metadata"]["name"],
                                  "uid": vcjob_uid,
                                  "blockOwnerDeletion": False,
                                  "controller": False,
                              }
                          ],
                      }
                  },
              )
          except Exception:
              from app.metrics import BACKEND_ERRORS

              BACKEND_ERRORS.labels(stage="token_secret_owner_patch").inc()
              logger.warning(
                  "token Secret ownerRef patch failed for job %s — relying on "
                  "reconciler sweep",
                  job.id,
                  exc_info=True,
              )
      else:
          logger.warning(
              "vcjob create response missing metadata.uid for job %s — "
              "skipping ownerRef patch (reconciler sweep is the fallback)",
              job.id,
          )

      job.k8s_job_name = manifest["metadata"]["name"]
      job.status = JobStatus.PREPARING
  ```

- [ ] **Step 5: Run the test.**

  ```bash
  cd backend && uv run pytest tests/test_jobs_dispatch_owner_ref.py -v
  ```

  Expected: PASS.

- [ ] **Step 6: Run the full suite.**

  ```bash
  cd backend && uv run pytest -q
  ```

  Expected: green. Existing dispatch tests may have been relying on the old
  `_StubVolcano.create_namespaced_custom_object` to return the body without
  uid — now it injects one. The only place this matters is dispatch itself,
  which is what we want.

- [ ] **Step 7: Commit.**

  ```bash
  git add backend/app/services/jobs_dispatch.py backend/tests/conftest.py \
    backend/tests/test_jobs_dispatch_owner_ref.py
  git commit -m "$(cat <<'EOF'
  fix(backend): patch job-token Secret with vcjob ownerReferences [M-token-secret-owner]

  After dispatch_job_to_volcano creates the vcjob, patch the matching
  job-token-<id> Secret with metadata.ownerReferences pointing at the
  vcjob (apiVersion batch.volcano.sh/v1alpha1, kind Job, name + uid from
  the create response). blockOwnerDeletion=False keeps a stuck Secret
  from blocking vcjob deletion; controller=False because vcjob is not a
  reconcile owner of Secret. K8s GC cascades the Secret removal when the
  vcjob is deleted via the normal path; the reconciler sweep in T12 is
  belt-and-suspenders for force-deletes (--grace-period=0).

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 12: [M-token-secret-owner part-b] Reconciler sweep for orphan `job-token-*` Secrets

**Findings:** M-token-secret-owner part-b (MEDIUM). Recommended model: **opus** (reconciler).

**Files:**

- Modify: `backend/app/reconciler/orphans.py`
- Create: `backend/tests/test_reconciler_token_secret_sweep.py`

**Rationale:** T11's `ownerReferences` covers the happy path (normal vcjob deletion). When an operator force-deletes a vcjob with `kubectl delete vcjob ... --grace-period=0 --force`, Kubernetes skips finalizers AND the GC controller never sees the deletion, so the owned Secret is left dangling. The sweep:

- Lists all Secrets named `job-token-*` in `JOB_NAMESPACE`.
- For each Secret older than `JOB_TTL_SECONDS_AFTER_FINISHED` (7 d) where the referenced vcjob is missing OR terminal — delete the Secret.
- Reuses the existing `reconcile_orphan_vcjobs` cadence (`ORPHAN_SCAN_EVERY_N_ITERATIONS = 30`, ~5 min) — sweep is co-located in `orphans.py` as a sibling function and invoked from `loop.py` alongside the vcjob scan.

- [ ] **Step 1: Write the failing test.**

  Create `backend/tests/test_reconciler_token_secret_sweep.py`:

  ```python
  """Tests for reconcile_orphan_token_secrets — sweeps job-token-* Secrets
  whose vcjob was force-deleted (--grace-period=0 skips GC)."""

  import uuid
  from datetime import UTC, datetime, timedelta
  from unittest.mock import patch

  import pytest


  def _secret(name: str, age_seconds: int) -> dict:
      created = (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()
      return {
          "metadata": {
              "name": name,
              "namespace": "lolday-jobs",
              "creationTimestamp": created.replace("+00:00", "Z"),
          }
      }


  class _CoreStub:
      def __init__(self, secrets: list[dict], deleted_record: list[str]):
          self._secrets = secrets
          self.deleted = deleted_record

      def list_namespaced_secret(self, namespace, **kw):
          class _R:
              pass
          r = _R()
          r.items = self._secrets
          return r

      def delete_namespaced_secret(self, name, namespace, **kw):
          self.deleted.append(name)


  class _VolcanoStub:
      def __init__(self, items: list[dict]):
          self._items = items

      def list_namespaced_custom_object(self, group, version, namespace, plural, **kw):
          return {"items": self._items}


  @pytest.mark.asyncio
  async def test_sweep_deletes_old_orphan_token_secrets(db_session):
      """Secret older than JOB_TTL_SECONDS_AFTER_FINISHED + no matching vcjob
      → deleted."""
      from app.reconciler.orphans import reconcile_orphan_token_secrets

      deleted: list[str] = []
      secrets = [
          _secret(f"job-token-{uuid.uuid4().hex[:16]}", age_seconds=7 * 86400 + 60),
      ]
      core = _CoreStub(secrets, deleted)
      volcano = _VolcanoStub([])

      with (
          patch("app.reconciler.orphans.core_v1", return_value=core),
          patch("app.reconciler.orphans.volcano_v1alpha1", return_value=volcano),
      ):
          n = await reconcile_orphan_token_secrets(db_session)

      assert n == 1
      assert deleted == [secrets[0]["metadata"]["name"]]


  @pytest.mark.asyncio
  async def test_sweep_keeps_young_secrets(db_session):
      """A Secret younger than the TTL must NOT be deleted — the parent vcjob
      may still be running and the GC hasn't fired yet."""
      from app.reconciler.orphans import reconcile_orphan_token_secrets

      deleted: list[str] = []
      young = _secret(f"job-token-{uuid.uuid4().hex[:16]}", age_seconds=60)
      core = _CoreStub([young], deleted)
      volcano = _VolcanoStub([])

      with (
          patch("app.reconciler.orphans.core_v1", return_value=core),
          patch("app.reconciler.orphans.volcano_v1alpha1", return_value=volcano),
      ):
          n = await reconcile_orphan_token_secrets(db_session)

      assert n == 0
      assert deleted == []


  @pytest.mark.asyncio
  async def test_sweep_keeps_secrets_with_live_vcjob(db_session):
      """A Secret whose name encodes a job-id matching a live vcjob must be
      kept, even if it's old. (Live vcjob → ownerRef GC will handle it on
      eventual deletion.)"""
      from app.reconciler.orphans import reconcile_orphan_token_secrets

      job_short = uuid.uuid4().hex[:16]
      secret_name = f"job-token-{job_short}"
      deleted: list[str] = []
      old = _secret(secret_name, age_seconds=7 * 86400 + 60)
      live_vcjob = {
          "metadata": {
              "name": f"job-train-{job_short}",
              "labels": {"lolday.job-id": str(uuid.UUID(job_short.ljust(32, "0")))},
          }
      }
      core = _CoreStub([old], deleted)
      volcano = _VolcanoStub([live_vcjob])

      with (
          patch("app.reconciler.orphans.core_v1", return_value=core),
          patch("app.reconciler.orphans.volcano_v1alpha1", return_value=volcano),
      ):
          # NOTE: the implementation correlates by job-short-id matching the
          # vcjob's lolday.job-id label prefix; if your implementation uses a
          # different matching key, adjust this fixture accordingly.
          n = await reconcile_orphan_token_secrets(db_session)

      assert n == 0
      assert deleted == []
  ```

- [ ] **Step 2: Run the failing tests.**

  ```bash
  cd backend && uv run pytest tests/test_reconciler_token_secret_sweep.py -v
  ```

  Expected: all three FAIL — `reconcile_orphan_token_secrets` does not exist.

- [ ] **Step 3: Implement the sweep.**

  Append to `backend/app/reconciler/orphans.py`:

  ```python
  TOKEN_SECRET_PREFIX = "job-token-"


  async def reconcile_orphan_token_secrets(session: AsyncSession) -> int:
      """Delete ``job-token-*`` Secrets whose parent vcjob is gone.

      The ``ownerReferences``-driven GC handles the happy path (vcjob deleted
      normally → Secret deleted by the K8s GC controller). This sweep catches
      the exception path: ``kubectl delete vcjob ... --grace-period=0 --force``
      removes the vcjob without firing finalizers or the GC controller,
      leaving the Secret as an orphan.

      We list every Secret in JOB_NAMESPACE matching the ``job-token-`` name
      prefix, check each one's age + whether a matching vcjob exists, and
      delete those that are both stale (older than
      ``JOB_TTL_SECONDS_AFTER_FINISHED``) and unowned (no matching vcjob).

      Returns the number of orphan Secrets deleted, for metrics.
      """
      secrets = await asyncio.to_thread(
          core_v1().list_namespaced_secret,
          namespace=settings.JOB_NAMESPACE,
      )
      vcjobs = await asyncio.to_thread(
          volcano_v1alpha1().list_namespaced_custom_object,
          group=VOLCANO_BATCH_GROUP,
          version=VOLCANO_BATCH_VERSION,
          namespace=settings.JOB_NAMESPACE,
          plural=VOLCANO_JOB_PLURAL,
      )

      # Build a set of live job-short-ids from the vcjob labels. The Secret
      # name pattern is ``job-token-<job.hex[:16]>``; the vcjob label
      # ``lolday.job-id`` carries the full UUID. Match on the 16-char prefix.
      live_short_ids: set[str] = set()
      for vj in vcjobs.get("items", []):
          label = (vj.get("metadata", {}).get("labels") or {}).get("lolday.job-id")
          if label:
              try:
                  live_short_ids.add(uuid.UUID(label).hex[:16])
              except ValueError:
                  continue

      now = datetime.now(UTC)
      ttl = settings.JOB_TTL_SECONDS_AFTER_FINISHED
      deleted = 0
      for sec in secrets.items:
          # Conftest stub passes dicts; real K8s passes objects. Handle both.
          meta = sec.get("metadata", {}) if isinstance(sec, dict) else (
              {
                  "name": sec.metadata.name,
                  "creationTimestamp": sec.metadata.creation_timestamp,
              }
          )
          name = meta.get("name", "")
          if not name.startswith(TOKEN_SECRET_PREFIX):
              continue
          # Age check.
          created_raw = meta.get("creationTimestamp")
          if isinstance(created_raw, datetime):
              created_at = created_raw
          elif isinstance(created_raw, str):
              try:
                  created_at = datetime.fromisoformat(
                      created_raw.replace("Z", "+00:00")
                  )
              except ValueError:
                  continue
          else:
              continue
          age = (now - created_at).total_seconds()
          if age < ttl:
              continue
          # Liveness check by short-id prefix.
          short_id = name.removeprefix(TOKEN_SECRET_PREFIX)
          if short_id in live_short_ids:
              continue
          # Delete.
          try:
              await asyncio.to_thread(
                  core_v1().delete_namespaced_secret,
                  name=name,
                  namespace=settings.JOB_NAMESPACE,
              )
              deleted += 1
              logger.info("deleted orphan job-token Secret %s (age=%.0fs)", name, age)
          except ApiException as exc:
              if exc.status != 404:
                  BACKEND_ERRORS.labels(stage="orphan_token_secret_delete").inc()
                  logger.warning(
                      "orphan token Secret %s delete returned %s",
                      name,
                      exc.status,
                      exc_info=True,
                  )
      return deleted
  ```

  Add the `uuid` import to the existing imports block at the top of the file
  (it's already there from `reconcile_orphan_vcjobs` — confirm before adding).

- [ ] **Step 4: Wire the sweep into the reconciler loop.**

  In `backend/app/reconciler/loop.py`, locate the orphan-scan block (the
  `if iteration % ORPHAN_SCAN_EVERY_N_ITERATIONS == 0:` branch). Add after
  the existing `reconcile_orphan_vcjobs` call:

  ```python
                  if iteration % ORPHAN_SCAN_EVERY_N_ITERATIONS == 0:
                      try:
                          await reconcile_orphan_vcjobs(session)
                      except Exception:
                          BACKEND_ERRORS.labels(stage="reconcile_orphan_vcjobs").inc()
                          logger.exception("reconcile_orphan_vcjobs failed")

                      try:
                          await reconcile_orphan_token_secrets(session)
                      except Exception:
                          BACKEND_ERRORS.labels(stage="reconcile_orphan_token_secrets").inc()
                          logger.exception("reconcile_orphan_token_secrets failed")
  ```

  Update the import at the top of `loop.py`:

  ```python
  from app.reconciler.orphans import (
      reconcile_orphan_token_secrets,
      reconcile_orphan_vcjobs,
  )
  ```

  Update `backend/app/reconciler/__init__.py` to re-export the new symbol:

  ```python
  from app.reconciler.orphans import (
      reconcile_orphan_token_secrets,
      reconcile_orphan_vcjobs,
  )

  __all__ = [
      # ... existing entries ...
      "reconcile_orphan_token_secrets",
      "reconcile_orphan_vcjobs",
      # ...
  ]
  ```

- [ ] **Step 5: Run the tests.**

  ```bash
  cd backend && uv run pytest tests/test_reconciler_token_secret_sweep.py -v
  ```

  Expected: all three pass.

- [ ] **Step 6: Run the full suite.**

  ```bash
  cd backend && uv run pytest -q
  ```

  Expected: green.

- [ ] **Step 7: Commit.**

  ```bash
  git add backend/app/reconciler/orphans.py backend/app/reconciler/loop.py \
    backend/app/reconciler/__init__.py \
    backend/tests/test_reconciler_token_secret_sweep.py
  git commit -m "$(cat <<'EOF'
  fix(backend): sweep orphan job-token Secrets via reconciler [M-token-secret-owner]

  Belt-and-suspenders for T11's ownerReferences-based GC: when an operator
  force-deletes a vcjob with --grace-period=0, the K8s GC controller does
  not fire and the Secret stays. reconcile_orphan_token_secrets lists
  every Secret in JOB_NAMESPACE with the job-token- prefix, deletes those
  older than JOB_TTL_SECONDS_AFTER_FINISHED (7 d) whose 16-char short-id
  no longer matches a live vcjob's lolday.job-id label. Co-located with
  reconcile_orphan_vcjobs in orphans.py; runs from the same
  ORPHAN_SCAN_EVERY_N_ITERATIONS=30 (~5 min) cadence.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 13: [L-harbor-robot-rotate part-a] Harbor robot `duration: -1` → `7776000`

**Findings:** L-harbor-robot-rotate part-a (LOW, lifecycle-critical). Recommended model: **sonnet**.

**Files:**

- Modify: `backend/app/services/harbor.py:62-95`
- Modify (add test): `backend/tests/test_services_harbor.py`

**Rationale:** `ensure_robot_account` currently creates the `build-pusher` robot with `duration: -1` (no expiry). The Harbor team recommends finite expirations for service accounts; with `-1` no operator action can trigger a rotation reminder. Change the default to `7776000` (90 days). The reconciler in T14 handles renewal + the first-time rotation of legacy `-1` robots.

- [ ] **Step 1: Write the failing test.**

  Append to `backend/tests/test_services_harbor.py`:

  ```python
  @pytest.mark.asyncio
  async def test_ensure_robot_account_uses_90_day_duration():
      """L-harbor-robot-rotate: new robots get a 90-day duration so the
      reconciler in T14 can renew them. -1 (no expiry) is forbidden."""
      with respx.mock(base_url="http://harbor") as mock:
          mock.get("/api/v2.0/robots").mock(return_value=httpx.Response(200, json=[]))
          create_route = mock.post("/api/v2.0/robots").mock(
              return_value=httpx.Response(
                  201, json={"name": "robot$build-pusher", "secret": "shh"}
              )
          )
          client = HarborClient("http://harbor", "admin", "pw")
          await client.ensure_robot_account("build-pusher", projects=["detectors"])

      # Inspect the JSON body sent in POST /robots.
      sent = create_route.calls.last.request
      import json as _json
      body = _json.loads(sent.content.decode())
      assert body["duration"] == 7776000  # 90d in seconds
  ```

- [ ] **Step 2: Run the failing test.**

  ```bash
  cd backend && uv run pytest tests/test_services_harbor.py::test_ensure_robot_account_uses_90_day_duration -v
  ```

  Expected: FAIL — current `duration: -1`.

- [ ] **Step 3: Change the duration.**

  In `backend/app/services/harbor.py` line ~90, replace `"duration": -1,` with:

  ```python
                      # L-harbor-robot-rotate: 90 days. The harbor_rotate
                      # reconciler (app/reconciler/harbor_rotate.py) renews
                      # quarterly + force-rotates any legacy duration=-1 robot
                      # left over from before this commit.
                      "duration": 7776000,
  ```

- [ ] **Step 4: Run the test.**

  ```bash
  cd backend && uv run pytest tests/test_services_harbor.py -v
  ```

  Expected: green.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/app/services/harbor.py backend/tests/test_services_harbor.py
  git commit -m "$(cat <<'EOF'
  fix(backend): set Harbor robot duration to 90 d [L-harbor-robot-rotate]

  Harbor robots created with duration=-1 never expire; without a finite
  expiry, no operator action can trigger a rotation reminder. Set the
  default to 7776000 s (90 d) so the reconciler in T14 can renew + the
  one-time legacy-robot force-rotation kicks in for existing -1 robots.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 14: [L-harbor-robot-rotate part-b] `reconciler/harbor_rotate.py` quarterly renew + legacy force-rotate

**Findings:** L-harbor-robot-rotate part-b (LOW, lifecycle-critical). Recommended model: **opus** (reconciler design + Harbor API integration).

**Files:**

- Modify: `backend/app/services/harbor.py` (add `get_robot`, `rotate_robot_secret`, `update_robot_duration`)
- Create: `backend/app/reconciler/harbor_rotate.py`
- Modify: `backend/app/reconciler/__init__.py` (re-export)
- Modify: `backend/app/reconciler/loop.py` (invoke from the loop)
- Create: `backend/tests/test_reconciler_harbor_rotate.py`

**Rationale:** Harbor's robot secret cannot be retrieved after creation — `GET /robots` returns the public metadata (name, id, duration, expires_at, permissions) but NOT the secret. To rotate, call `PATCH /robots/{id}/sec` which generates a fresh secret and returns it. To extend the expiry of an existing robot, call `PUT /robots/{id}` with the full robot body and the new `duration`. The reconciler:

- Runs every `HARBOR_ROTATE_EVERY_N_ITERATIONS` iterations of the main reconciler loop (~24 h at 10 s/iter × 8640 iter — cheap).
- Calls `harbor_client.get_robot("build-pusher")` to retrieve the current state.
- Decision logic:
  - If `robot.duration == -1` (legacy "no expiry") → force-rotate (this is the **one-time cutover** for the existing build-pusher robot per user decision).
  - Otherwise, parse `expires_at` (Harbor epoch seconds); if expiry is < 30 d away → rotate.
  - Otherwise → no-op.
- Rotation steps: call `update_robot_duration(robot_id, 7776000)` to extend, then `rotate_robot_secret(robot_id)` to get a new secret, then `_write_docker_config_secret` (reused from `harbor_init.py`) to update the in-cluster `harbor-push-cred` Secret.

- [ ] **Step 1: Extend `harbor.py` with new client methods.**

  Append to the `HarborClient` class in `backend/app/services/harbor.py`:

  ```python
      async def get_robot(self, name: str) -> dict | None:
          """Fetch the robot record by short-name (without ``robot$`` prefix).

          Returns ``None`` if no matching robot exists. Returned dict has keys
          ``id``, ``name`` (with ``robot$`` prefix), ``duration``, ``expires_at``,
          ``permissions``. The ``secret`` field is NOT returned — Harbor never
          discloses an existing secret.
          """
          async with self._client() as c:
              resp = await c.get("/api/v2.0/robots", params={"q": f"name={name}"})
              resp.raise_for_status()
              expected = f"robot${name}"
              for r in resp.json():
                  if r.get("name") == expected:
                      return r
              return None

      async def rotate_robot_secret(self, robot_id: int) -> str:
          """Generate a fresh secret for the robot; returns the new secret value.

          Idempotent in the sense that running it twice generates two distinct
          secrets, both valid (Harbor returns the most-recently-rotated value).
          """
          async with self._client() as c:
              resp = await c.patch(f"/api/v2.0/robots/{robot_id}/sec")
              resp.raise_for_status()
              data = resp.json()
              # Harbor PATCH /robots/{id}/sec response shape: {"secret": "..."}
              return data["secret"]

      async def update_robot_duration(
          self, robot_id: int, duration_seconds: int
      ) -> None:
          """Reset the robot's expiry by PUTting the full record with a new
          ``duration``. Harbor recomputes ``expires_at`` from ``now + duration``."""
          async with self._client() as c:
              # Harbor requires the full robot body on PUT — fetch current state first.
              cur = await c.get(f"/api/v2.0/robots/{robot_id}")
              cur.raise_for_status()
              body = cur.json()
              body["duration"] = duration_seconds
              # ``editable`` field, if present, is read-only — drop it from PUT.
              body.pop("editable", None)
              body.pop("expires_at", None)
              put = await c.put(f"/api/v2.0/robots/{robot_id}", json=body)
              put.raise_for_status()
  ```

- [ ] **Step 2: Write the failing tests.**

  Create `backend/tests/test_reconciler_harbor_rotate.py`:

  ```python
  """Tests for app.reconciler.harbor_rotate — quarterly renewal + one-time
  force-rotate of legacy duration=-1 robots."""

  from datetime import UTC, datetime, timedelta
  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest


  @pytest.mark.asyncio
  async def test_reconcile_force_rotates_legacy_duration_neg1_robot():
      """L-harbor-robot-rotate: a robot with duration=-1 (legacy, never expires)
      is force-rotated unconditionally on the first reconciler pass."""
      from app.reconciler import harbor_rotate

      mock_client = MagicMock()
      mock_client.get_robot = AsyncMock(
          return_value={
              "id": 42,
              "name": "robot$build-pusher",
              "duration": -1,
              "expires_at": -1,
          }
      )
      mock_client.update_robot_duration = AsyncMock()
      mock_client.rotate_robot_secret = AsyncMock(return_value="fresh-secret")

      written = []

      async def fake_writer(name, secret):
          written.append((name, secret))

      with (
          patch("app.reconciler.harbor_rotate.HarborClient", return_value=mock_client),
          patch(
              "app.reconciler.harbor_rotate._write_docker_config_secret",
              fake_writer,
          ),
      ):
          rotated = await harbor_rotate.reconcile_harbor_robot()

      assert rotated is True
      mock_client.update_robot_duration.assert_awaited_once_with(42, 7776000)
      mock_client.rotate_robot_secret.assert_awaited_once_with(42)
      assert written == [("robot$build-pusher", "fresh-secret")]


  @pytest.mark.asyncio
  async def test_reconcile_rotates_robot_within_30_day_threshold():
      """A robot that expires in <30 d is rotated."""
      from app.reconciler import harbor_rotate

      soon = int((datetime.now(UTC) + timedelta(days=15)).timestamp())
      mock_client = MagicMock()
      mock_client.get_robot = AsyncMock(
          return_value={"id": 7, "name": "robot$build-pusher", "duration": 7776000, "expires_at": soon}
      )
      mock_client.update_robot_duration = AsyncMock()
      mock_client.rotate_robot_secret = AsyncMock(return_value="s")

      with (
          patch("app.reconciler.harbor_rotate.HarborClient", return_value=mock_client),
          patch(
              "app.reconciler.harbor_rotate._write_docker_config_secret",
              AsyncMock(),
          ),
      ):
          rotated = await harbor_rotate.reconcile_harbor_robot()

      assert rotated is True


  @pytest.mark.asyncio
  async def test_reconcile_skips_robot_outside_threshold():
      """A robot expiring in >30 d is left alone."""
      from app.reconciler import harbor_rotate

      far = int((datetime.now(UTC) + timedelta(days=60)).timestamp())
      mock_client = MagicMock()
      mock_client.get_robot = AsyncMock(
          return_value={"id": 7, "name": "robot$build-pusher", "duration": 7776000, "expires_at": far}
      )
      mock_client.update_robot_duration = AsyncMock()
      mock_client.rotate_robot_secret = AsyncMock(return_value="s")

      with (
          patch("app.reconciler.harbor_rotate.HarborClient", return_value=mock_client),
          patch(
              "app.reconciler.harbor_rotate._write_docker_config_secret",
              AsyncMock(),
          ),
      ):
          rotated = await harbor_rotate.reconcile_harbor_robot()

      assert rotated is False
      mock_client.rotate_robot_secret.assert_not_awaited()


  @pytest.mark.asyncio
  async def test_reconcile_noop_when_robot_missing():
      """If the build-pusher robot doesn't exist yet (init_harbor hasn't run),
      reconcile_harbor_robot is a no-op — harbor_init creates the robot on
      backend startup."""
      from app.reconciler import harbor_rotate

      mock_client = MagicMock()
      mock_client.get_robot = AsyncMock(return_value=None)
      mock_client.rotate_robot_secret = AsyncMock()

      with patch("app.reconciler.harbor_rotate.HarborClient", return_value=mock_client):
          rotated = await harbor_rotate.reconcile_harbor_robot()

      assert rotated is False
      mock_client.rotate_robot_secret.assert_not_awaited()
  ```

- [ ] **Step 3: Run the failing tests.**

  ```bash
  cd backend && uv run pytest tests/test_reconciler_harbor_rotate.py -v
  ```

  Expected: all four FAIL — module doesn't exist.

- [ ] **Step 4: Create `backend/app/reconciler/harbor_rotate.py`.**

  ```python
  """Harbor robot account rotation.

  Renews the ``build-pusher`` robot's secret and resets its 90-day expiry
  whenever it's within ``HARBOR_ROBOT_RENEW_THRESHOLD_SECONDS`` of expiry.
  Also force-rotates the one-time legacy ``duration=-1`` (no-expiry) robot
  left over from before T13 — on the first reconciler tick after this code
  ships, that robot gets a fresh secret + a finite 90-day expiry.

  Invoked from :func:`app.reconciler.loop.reconciler_loop` every
  ``HARBOR_ROTATE_EVERY_N_ITERATIONS`` iterations (~24 h at the default 10 s
  reconciler tick). The check is cheap (one GET /robots) so daily cadence
  is fine — the actual rotation happens at most quarterly per robot.
  """

  from __future__ import annotations

  import logging
  from datetime import UTC, datetime

  from app.config import settings
  from app.metrics import BACKEND_ERRORS
  from app.services.harbor import HarborClient
  from app.services.harbor_init import ROBOT_NAME, _write_docker_config_secret

  logger = logging.getLogger(__name__)

  HARBOR_ROBOT_RENEW_DURATION_SECONDS = 7776000  # 90 d (matches harbor.py default)
  HARBOR_ROBOT_RENEW_THRESHOLD_SECONDS = 30 * 86400  # rotate when <30 d remaining


  async def reconcile_harbor_robot() -> bool:
      """Decide whether to rotate the build-pusher robot; rotate if needed.

      Returns True if a rotation was performed, False otherwise (no robot
      exists yet, or expiry is comfortably in the future). Exceptions
      propagate to the reconciler loop's per-iteration try/except (which
      counts the error and logs).
      """
      if not settings.HARBOR_ADMIN_PASSWORD:
          # Identical guard to init_harbor — test envs run without Harbor.
          return False

      client = HarborClient(
          settings.HARBOR_URL,
          settings.HARBOR_ADMIN_USERNAME,
          settings.HARBOR_ADMIN_PASSWORD,
      )
      robot = await client.get_robot(ROBOT_NAME)
      if robot is None:
          # Robot doesn't exist yet — init_harbor (lifespan) is the right place
          # to create it. We're a renewal loop, not a bootstrap.
          return False

      duration = robot.get("duration", 0)
      expires_at = robot.get("expires_at", 0)
      robot_id = robot["id"]
      now_epoch = int(datetime.now(UTC).timestamp())

      # Legacy duration=-1 robot: force-rotate on first pass after T13.
      legacy_neg1 = duration == -1
      # Normal renewal: <30 d remaining.
      expiring_soon = (
          expires_at > 0
          and (expires_at - now_epoch) < HARBOR_ROBOT_RENEW_THRESHOLD_SECONDS
      )

      if not (legacy_neg1 or expiring_soon):
          return False

      reason = "legacy duration=-1" if legacy_neg1 else f"expires in {(expires_at - now_epoch) // 86400} d"
      logger.info("rotating Harbor robot %s (reason: %s)", robot["name"], reason)

      try:
          # Extend / reset the expiry first — if rotate_robot_secret succeeds and
          # update fails, we'd leave the next reconciler tick to retry from a
          # known-good state (the new secret is already in the Harbor record).
          await client.update_robot_duration(
              robot_id, HARBOR_ROBOT_RENEW_DURATION_SECONDS
          )
          new_secret = await client.rotate_robot_secret(robot_id)
      except Exception:
          BACKEND_ERRORS.labels(stage="harbor_robot_rotate_api").inc()
          raise

      try:
          await _write_docker_config_secret(robot["name"], new_secret)
      except Exception:
          BACKEND_ERRORS.labels(stage="harbor_robot_rotate_k8s").inc()
          logger.exception(
              "harbor robot rotated but harbor-push-cred Secret write failed — "
              "next build will use the new secret only after the Secret is "
              "manually re-applied"
          )
          raise

      logger.info("Harbor robot %s rotated (new expiry +90 d)", robot["name"])
      return True
  ```

- [ ] **Step 5: Wire into the reconciler loop.**

  In `backend/app/reconciler/loop.py`, add a constant near the existing
  iteration constants:

  ```python
  HARBOR_ROTATE_EVERY_N_ITERATIONS = 8640  # ~24 h at the default 10 s tick
  ```

  Add the call inside the `reconciler_loop` async session block, after the
  orphan-token-secret sweep:

  ```python
                  if iteration % HARBOR_ROTATE_EVERY_N_ITERATIONS == 0:
                      try:
                          await reconcile_harbor_robot()
                      except Exception:
                          BACKEND_ERRORS.labels(stage="reconcile_harbor_robot").inc()
                          logger.exception("reconcile_harbor_robot failed")
  ```

  Import:

  ```python
  from app.reconciler.harbor_rotate import reconcile_harbor_robot
  ```

  Update `backend/app/reconciler/__init__.py` to re-export `reconcile_harbor_robot`
  (mirroring the existing pattern).

- [ ] **Step 6: Run the tests.**

  ```bash
  cd backend && uv run pytest tests/test_reconciler_harbor_rotate.py -v
  ```

  Expected: all four pass.

- [ ] **Step 7: Run the full suite.**

  ```bash
  cd backend && uv run pytest -q
  ```

  Expected: green.

- [ ] **Step 8: Commit.**

  ```bash
  git add backend/app/services/harbor.py backend/app/reconciler/harbor_rotate.py \
    backend/app/reconciler/loop.py backend/app/reconciler/__init__.py \
    backend/tests/test_reconciler_harbor_rotate.py
  git commit -m "$(cat <<'EOF'
  feat(backend): reconciler rotates Harbor robot every ~90 d [L-harbor-robot-rotate]

  reconcile_harbor_robot runs daily from the main reconciler loop. Decides
  whether to rotate the build-pusher robot:
    - duration=-1 (legacy "never expires") → force-rotate immediately
      (the one-time cutover for the existing robot after T13).
    - expires_at - now < 30 d → rotate.
    - otherwise → no-op.
  Rotation: update_robot_duration(90 d) → rotate_robot_secret →
  _write_docker_config_secret to refresh in-cluster harbor-push-cred.
  The first tick after this code ships fixes the existing duration=-1
  robot in-place.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 15: [L-minio-key-rotate] Operator script for MinIO svcacct AK/SK rotation

**Findings:** L-minio-key-rotate (LOW, lifecycle-critical). Recommended model: **sonnet**.

**Files:**

- Create: `scripts/rotate-minio-keys.sh`
- Modify: `charts/lolday/templates/minio-init-buckets-job.yaml` (charset comment)
- Modify: `docs/runbooks/storage-migration.md` or new `docs/runbooks/minio-key-rotation.md` (decide based on existing doc structure — prefer new file)

**Rationale:** MinIO svcacct keys are produced once by `templates/minio-init-buckets-job.yaml` on first install and then never rotate (the bitnami/kubectl writer Role intentionally omits update/patch — see the chart comment). For lifecycle hygiene, the operator runs a standalone script that:

- Talks to MinIO via `mc admin user svcacct` (port-forward to `lolday-minio:9000`).
- For each app (mlflow, harbor, loki):
  - Generates a fresh AK/SK pair using `openssl rand -base64 30 | tr -d '/+=' | head -c 40` (alphanum-ish charset matching what the init-job uses; openssl is more entropic than the init-job's `tr -dc 'a-zA-Z0-9' </dev/urandom`).
  - `mc admin user svcacct add` with the new pair attached to the same policy.
  - Updates the K8s Secret (`mlflow-s3`, `registry-s3`, `loki-s3`).
  - Rolls the consumer Deployment so it picks up the new env.
  - Removes the OLD svcacct after the rollout completes.

Init-job's existing charset is functionally equivalent (alphanum) — the comment update is documentation-only. The actual rotation lives in the script.

- [ ] **Step 1: Create `scripts/rotate-minio-keys.sh`.**

  ```bash
  #!/usr/bin/env bash
  # rotate-minio-keys.sh — generate fresh MinIO svcacct AK/SK for mlflow / harbor / loki
  # consumers, update the matching K8s Secrets, roll the Deployments.
  #
  # Required env (sourced from .lolday-secrets.env or shell):
  #   MINIO_ROOT_USER, MINIO_ROOT_PASSWORD  (MinIO root creds for mc admin)
  #
  # Usage:
  #   bash scripts/rotate-minio-keys.sh         # rotate all three
  #   bash scripts/rotate-minio-keys.sh mlflow  # rotate one app only
  #
  # Spec: docs/superpowers/specs/2026-05-12-security-hardening-design.md §6.3
  set -euo pipefail

  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  SECRETS=${SECRETS:-${REPO_ROOT}/.lolday-secrets.env}
  [ -f "$SECRETS" ] || SECRETS="$HOME/.lolday-secrets.env"
  if [ -f "$SECRETS" ]; then
    # shellcheck disable=SC1090
    source "$SECRETS"
  fi
  : "${MINIO_ROOT_USER:?MINIO_ROOT_USER required (MinIO root username from minio Helm release)}"
  : "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD required (MinIO root password)}"

  APPS=("${@:-mlflow harbor loki}")

  # ---------- helpers ----------

  _gen_key() {
    # 40 chars from the alphanum-after-base64 charset. matches the init-job's
    # `tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 40` distribution but uses
    # openssl rand for a deterministic entropy source.
    openssl rand -base64 30 | tr -d '/+=' | head -c 40
  }

  _secret_name() {
    case "$1" in
      mlflow) echo "mlflow-s3" ;;
      harbor) echo "registry-s3" ;;
      loki) echo "loki-s3" ;;
      *) echo "unknown-app-$1" ;;
    esac
  }

  _ak_key()  { case "$1" in harbor) echo "REGISTRY_STORAGE_S3_ACCESSKEY" ;; *) echo "access-key" ;; esac; }
  _sk_key()  { case "$1" in harbor) echo "REGISTRY_STORAGE_S3_SECRETKEY" ;; *) echo "secret-key" ;; esac; }
  _consumer_deployment() {
    case "$1" in
      mlflow) echo "deploy/lolday-mlflow" ;;
      harbor) echo "deploy/lolday-harbor-registry" ;;
      loki)   echo "statefulset/loki" ;;
    esac
  }

  # ---------- port-forward MinIO ----------

  echo "[1/3] starting kubectl port-forward to MinIO :9000…"
  kubectl -n lolday port-forward svc/lolday-minio 9000:9000 >/dev/null 2>&1 &
  PF_PID=$!
  trap 'kill $PF_PID 2>/dev/null || true' EXIT
  sleep 3   # give the forward time to bind

  mc alias set rot http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" \
    >/dev/null

  # ---------- per-app rotation ----------

  echo "[2/3] rotating svcaccts…"
  for app in $APPS; do
    SECRET=$(_secret_name "$app")
    AK_KEY=$(_ak_key "$app")
    SK_KEY=$(_sk_key "$app")
    DEPLOY=$(_consumer_deployment "$app")

    echo "  --- $app ---"
    NEW_AK=$(_gen_key)
    NEW_SK=$(_gen_key)

    # Stage to a tmp dir for safe kubectl input.
    TMP=$(mktemp -d); chmod 700 "$TMP"
    printf '%s' "$NEW_AK" > "$TMP/ak"
    printf '%s' "$NEW_SK" > "$TMP/sk"
    chmod 600 "$TMP/ak" "$TMP/sk"

    # Find the old AK from the existing Secret so we can revoke it after rollout.
    OLD_AK=$(kubectl -n lolday get secret "$SECRET" -o jsonpath="{.data.${AK_KEY}}" \
      | base64 -d 2>/dev/null || echo "")

    # 2a. Create the new svcacct in MinIO.
    mc admin user svcacct add rot "$MINIO_ROOT_USER" \
      --access-key "$NEW_AK" --secret-key "$NEW_SK" \
      --policy "${app}-rw"

    # 2b. Replace the K8s Secret (dry-run | apply pattern, same as deploy.sh).
    kubectl -n lolday create secret generic "$SECRET" \
      --from-file="${AK_KEY}=$TMP/ak" --from-file="${SK_KEY}=$TMP/sk" \
      --dry-run=client -o yaml | kubectl apply -f -

    # 2c. Roll the consumer to pick up the new env.
    kubectl -n lolday rollout restart "$DEPLOY"
    kubectl -n lolday rollout status "$DEPLOY" --timeout=5m

    # 2d. Revoke the OLD svcacct now that the consumer is using NEW.
    if [ -n "$OLD_AK" ] && [ "$OLD_AK" != "$NEW_AK" ]; then
      echo "    revoking OLD AK=${OLD_AK:0:6}…"
      mc admin user svcacct rm rot "$OLD_AK" || true   # already-deleted is OK
    fi

    shred -u "$TMP/ak" "$TMP/sk"; rmdir "$TMP"
  done

  echo "[3/3] done."
  ```

  After writing, mark it executable:

  ```bash
  chmod +x scripts/rotate-minio-keys.sh
  ```

- [ ] **Step 2: Update the init-job charset comment.**

  In `charts/lolday/templates/minio-init-buckets-job.yaml`, find the AK/SK
  generation lines (currently `AK=$(tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 20)`
  and the corresponding 40-char SK). Add a comment block just above them:

  ```yaml
  # L-minio-key-rotate: the init-job is bootstrap-only. After
  # first install, key rotation is an operator action via
  # scripts/rotate-minio-keys.sh — the bitnami/kubectl writer
  # Role intentionally omits update/patch on Secrets so this
  # init-job NEVER overwrites an existing key on subsequent
  # helm upgrades. The charset below is alphanum (a-zA-Z0-9);
  # the rotation script uses `openssl rand -base64 30 |
  # tr -d '/+='` which produces the same distribution from a
  # deterministic entropy source.
  ```

- [ ] **Step 3: Bash syntax-check the script.**

  ```bash
  bash -n scripts/rotate-minio-keys.sh
  ```

  Expected: clean.

- [ ] **Step 4: helm-render the chart and confirm the comment lands.**

  ```bash
  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test 2>/dev/null \
    | grep -A 2 'L-minio-key-rotate' | head -5
  ```

  Expected: shows the comment line(s).

- [ ] **Step 5: Write the operator note.**

  Add an entry to the PR body when opening the phase:

  > Operator action after merge:
  >
  > 1. Run `bash scripts/rotate-minio-keys.sh` once on server30 (or the operator
  >    workstation with `kubectl` context pointed at server30).
  > 2. Verify each consumer is healthy: `kubectl -n lolday get pods | grep -E 'mlflow|harbor|loki'`.
  > 3. Confirm `mc admin user svcacct ls rot $MINIO_ROOT_USER` shows three
  >    svcaccts (mlflow, harbor, loki) — no leftover from before the rotation.

- [ ] **Step 6: Commit.**

  ```bash
  git add scripts/rotate-minio-keys.sh charts/lolday/templates/minio-init-buckets-job.yaml
  git commit -m "$(cat <<'EOF'
  feat(scripts): rotate-minio-keys.sh for MinIO svcacct rotation [L-minio-key-rotate]

  The init-job creates MinIO svcacct AK/SK once per fresh install; the
  bitnami/kubectl writer Role intentionally omits update/patch, so
  rotation is an explicit operator action — not an init-job side effect.
  scripts/rotate-minio-keys.sh per-app (mlflow / harbor / loki):
    - generate AK/SK via openssl rand -base64 30 | tr -d '/+=' | head -c 40
    - mc admin user svcacct add (under same -rw policy)
    - kubectl create secret --from-file (matches T4 / recover-harbor.sh
      pattern, never via argv)
    - rollout restart + status the consumer
    - revoke the OLD svcacct after the consumer is healthy
  Init-job charset comment updated to point to the rotation script.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## P3 Done

After Task 15 lands, verify the whole phase end-to-end:

- [ ] **Step A: Full backend test suite.**

  ```bash
  cd backend && uv run pytest -q
  ```

  Expected: green. Count delta from Pre-flight (new tests: ~13 — 2 in notify, 1 in test_config (H-17a), 5 in test_config (H-17b/H-18b), 4 in crypto, 3 in rotate_fernet, 1 in test_jobs_dispatch_owner_ref, 3 in test_reconciler_token_secret_sweep, 1 in test_services_harbor, 4 in test_reconciler_harbor_rotate = ~24 added).

- [ ] **Step B: helm lint with the post-T8 flag set.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test \
    --set backend.fernetKeys=test \
    --set postgresql.auth.password=test \
    --set mlflow.auth.password=test --set mlflow.db.password=test \
    --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: clean.

- [ ] **Step C: pre-commit on all files.**

  ```bash
  pre-commit run --all-files
  ```

  Expected: clean. If any hook complains about line length / ruff format on
  the new code, fix in-place and amend the relevant commit. **Do NOT use
  `--no-verify`** (per project hard rule).

- [ ] **Step D: Cross-check finding IDs in commit history.**

  ```bash
  git log --oneline main..HEAD | grep -oE '\[[A-Z][^]]+\]' | tr ',' '\n' | sort -u | tr -d '[]'
  ```

  Expected output (set):

  ```
  H-17a
  H-17b
  H-18
  H-18a
  H-18b
  H-19
  H-22
  L-harbor-robot-rotate
  L-minio-key-rotate
  M-deploy-from-literal
  M-discord-log
  M-pg-exporter
  M-token-secret-owner
  ```

- [ ] **Step E: Open the PR.**

  Push the branch + `gh pr create --base main`. PR body must call out:
  - **Breaking change (operator action required pre-deploy):** rename `FERNET_KEY=$X` to `FERNET_KEYS=$X` in `.lolday-secrets.env`. The chart's `validate_fernet_keys` validator hard-fails boot if `FERNET_KEYS` is empty or contains the legacy public test key.
  - **Operator action post-merge — Fernet rotation:** OPTIONAL but recommended. Follow `docs/runbooks/p3-fernet-rotation.md` to rotate the existing key (the same-value rename does NOT change the key; this PR simply makes rotation possible).
  - **Operator action post-merge — Cloudflare Access backups:** re-encrypt the existing `.lolday-cloudflare-access-backups/*.json` cleartext files per `docs/runbooks/cf-access-backups.md` § "Migrate existing cleartext snapshots", then `shred -u` them.
  - **Operator action post-merge — MinIO key rotation:** OPTIONAL (one-time cutover): run `bash scripts/rotate-minio-keys.sh` to swap mlflow / harbor / loki svcacct AK/SK pairs to fresh values generated via `openssl rand`.
  - **Auto-cutover — Harbor robot:** the existing `build-pusher` robot has `duration=-1` (legacy). The first reconciler tick after deploy (≤24 h, default cadence) detects this and force-rotates: PUT `/robots/{id}` to set 90 d expiry + PATCH `/robots/{id}/sec` to generate a fresh secret + update `harbor-push-cred` Secret. No operator action.
  - **Build pipeline behavior change:** the clone initContainer now uses git credential-helper instead of inline URL-PAT. Drain in-flight builds before merging.

- [ ] **Step F: Post-deploy operator verification.**

  ```bash
  # FERNET_KEYS env is plural and non-empty.
  kubectl -n lolday get deploy backend -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="FERNET_KEYS")]}'

  # Discord webhook log redaction — trigger a failure and grep Loki:
  # (curl Discord webhook with a bogus payload to force 400/500, then:)
  kubectl -n monitoring logs -l app.kubernetes.io/name=loki --tail 200 | grep "Discord notify" | head -5
  # Expected: lines with "status=4XX host=discord.com" — NO full URL.

  # Harbor robot rotation: first reconciler tick should rotate the -1 robot.
  kubectl -n lolday logs deploy/backend --tail 200 | grep -i "rotating Harbor robot"
  # Expected (within the first 24 h after deploy): a single info line.

  # job-token Secret ownerRef on a freshly-submitted job:
  kubectl -n lolday-jobs get secret -o json \
    | jq '.items[] | select(.metadata.name | startswith("job-token-")) | .metadata.ownerReferences'
  # Expected: each Secret carries one ownerReference (kind Job, apiVersion batch.volcano.sh/v1alpha1).

  # postgres-exporter Secret keys:
  kubectl -n lolday describe secret postgres-exporter-db | head
  # Expected: DATA_SOURCE_USER, DATA_SOURCE_PASS, DATA_SOURCE_URI, password — no DATA_SOURCE_NAME.
  ```

---

## Notes for the implementer

- **Pydantic env-list parsing** — `FERNET_KEYS: list[str]` with a `field_validator(mode="before")` is the cleanest split. pydantic-settings by default may try JSON-parsing a list field, which fails on a plain whitespace-separated string. The `before` validator runs ahead of pydantic's type coercion and converts the raw env string into a list — after that the field is just a `list[str]` and the model validator's check is plain.
- **MultiFernet semantics, gotcha** — `MultiFernet` encrypts with the FIRST key. The operator runbook (T10) is explicit: deploy `FERNET_KEYS="$NEW $OLD"`, the NEW key is first, so new writes are under NEW. Decrypt scans the full list, so OLD-encrypted rows still read. After `rotate_fernet.py` re-encrypts every row, retire OLD by deploying `FERNET_KEYS="$NEW"`. Reversing the order (`"$OLD $NEW"`) silently puts new writes under OLD — defensive: do not let any documentation example say that.
- **Volcano `Job` resource kind** — Volcano uses `kind: Job` under `apiVersion: batch.volcano.sh/v1alpha1`, NOT the core `batch/v1` Job. The ownerReferences `apiVersion + kind` pair must match exactly or K8s GC refuses to honor it. T11's `body["metadata"]["ownerReferences"][0]` uses `apiVersion: f"{VOLCANO_BATCH_GROUP}/{VOLCANO_BATCH_VERSION}"` and `kind: "Job"`.
- **Harbor PUT vs PATCH for duration** — Harbor's `PATCH /robots/{id}` is documented but its semantics for `duration` are inconsistent across versions; `PUT /robots/{id}` with the full body is the path `recover-harbor.sh` already uses for `permissions`, so we follow it. The `update_robot_duration` helper drops `editable` + `expires_at` from the PUT body (Harbor recomputes both server-side).
- **Conftest stub for `_StubVolcano.create_namespaced_custom_object`** — injecting `metadata.uid` is the simplest change; alternative would be to mock per-test. The injection only affects code that READs `vcjob_resp["metadata"]["uid"]`, which is exclusively the new T11 code. Existing reconciler tests construct vcjob bodies in their own fixtures and never call `create_namespaced_custom_object`, so they're unaffected.
- **Per-task TDD** — every backend code task uses `pytest -v` for the failing-test step and again for the passing-test step. Chart-only tasks use `helm lint` + `helm template | grep` to verify. Scripts use `bash -n` for syntax.
- **Model selection per task** (recommended; pass via `--model` to subagent):
  - **sonnet** — T1, T2, T3, T4, T5, T6, T10, T13, T15 (small, isolated, documentation, or one-file changes)
  - **opus** — T7, T8, T9, T11, T12, T14 (design judgment, multi-file, cipher semantics, reconciler integration)

---

## Self-review (writing-plans skill)

**Spec coverage** — every P3 finding from spec §6.3 maps to a task:

| Finding                        | Tasks              |
| ------------------------------ | ------------------ |
| H-17 (conftest + Settings)     | T6, T8             |
| H-18 (TokenCipher MultiFernet) | T7                 |
| H-18a (rotate_fernet.py)       | T9 (+ T10 runbook) |
| H-18b (Settings.FERNET_KEYS)   | T8                 |
| H-19 (git PAT helper)          | T5                 |
| H-22 (cf-access backups age)   | T3                 |
| M-deploy-from-literal          | T4                 |
| M-discord-log                  | T1                 |
| M-token-secret-owner           | T11 (a) + T12 (b)  |
| M-pg-exporter                  | T2                 |
| L-harbor-robot-rotate          | T13 (a) + T14 (b)  |
| L-minio-key-rotate             | T15                |

**Placeholder scan:** every code step contains the actual code; every shell step contains the exact command + expected output; every commit shows the full HEREDOC body. No "TBD" / "implement later" markers.

**Type consistency:**

- `TokenCipher.__init__` parameter is `keys: str | bytes | Iterable[str | bytes]` (T7) — every caller (`credentials.py`, `detectors.py`, `rotate_fernet.py`, all tests) passes a value in that union.
- `Settings.FERNET_KEYS: list[str]` (T8) — callers consume as `list[str]`; `TokenCipher(list[str])` is the iterable path, matches T7.
- `rotate_all(old_key: str, new_key: str) -> tuple[int, int]` (T9) — tests assert the tuple shape; the CLI logs both fields.
- `reconcile_orphan_token_secrets(session: AsyncSession) -> int` (T12) — returns count, matches `reconcile_orphan_vcjobs`'s shape.
- `reconcile_harbor_robot() -> bool` (T14) — returns whether a rotation happened; reconciler loop ignores the value (only used in tests).

**Known fragilities:**

- T11 (M-token-secret-owner ownerRef) — the K8s API server requires the parent resource (vcjob) to exist when the ownerRef is set. T11 patches the Secret AFTER `create_namespaced_custom_object` returns successfully, so the vcjob is guaranteed to exist. Race window: the Secret is created BEFORE the vcjob (T11 preserves the existing order), so during the ~10–50 ms gap between Secret-create and vcjob-create, the Secret has no owner. If `create_namespaced_custom_object` fails, the existing rollback deletes the Secret. No user-visible gap.
- T12 (M-token-secret-owner sweep) — the matching key between Secret name (`job-token-<job.hex[:16]>`) and vcjob's `lolday.job-id` label (full UUID) is the 16-char prefix. A theoretical UUID collision in the first 16 hex chars (probability ≈ 2⁻⁶⁴ per pair) could keep an orphan Secret alive. Acceptable for a 7-day TTL belt-and-suspenders.
- T14 (Harbor robot rotation) — Harbor's `expires_at` returned by `GET /robots` for a `duration=-1` robot has been observed as `-1`, `0`, or `null` across Harbor versions. The legacy-cutover branch keys off `duration == -1` (not `expires_at`), which is the stable signal.
- T9 (rotate_fernet.py) — uses `app.db.async_session_maker`, which is bound to `settings.DATABASE_URL` at module import time. When running `python -m app.scripts.rotate_fernet` inside the backend pod, the env is what helm deployed (production DSN). Tests monkeypatch `rotate_fernet.async_session_maker` to point at the test sqlite session_maker. The script's coupling to module-global state is acceptable for a one-shot CLI.

---
