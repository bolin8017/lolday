# Discord Notification Audit — Implementation Plan

> **Built 2026-05-17.** Implementation plan for the design in
> `docs/superpowers/specs/2026-05-17-discord-notification-audit-design.md`.
> Mini-PR cadence (precedent: #201–#206 same-day on 2026-05-16).
> Sequential execution; each PR is template-only or docs-only and ships
> without a `Chart.yaml` bump (per `.claude/rules/charts-and-helm.md`).

## Status legend

- ⬜ pending — not started
- 🔄 in-flight — branch open, PR pushed
- ✅ shipped — squash-merged to `main`
- ⏭ deferred — see spec §10

## PR pipeline (sequential)

| #   | PR title                                                                                           | Scope                                                | Files touched                                                                                                               | Status |
| --- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------ |
| A   | `fix(charts): route severity=info alerts to null receiver — sister to #206`                        | alertmanager-config-discord template + helm-unittest | `charts/lolday/templates/monitoring/alertmanager-config-discord.yaml` + `charts/lolday/tests/alertmanagerconfig_test.yaml`  | ⬜     |
| B   | `fix(charts): drop unused volcano-scheduler ServiceMonitor — root-cause TargetDown noise`          | template removal                                     | `charts/lolday/templates/monitoring/servicemonitor-volcano.yaml` (delete) + `.claude/rules/charts-and-helm.md` (table edit) | ⬜     |
| C   | `feat(monitoring): LoldayDiscordNotifyDropped alert — closes M-notify-semaphore observability gap` | alertmanager-rules template + helm-unittest          | `charts/lolday/templates/monitoring/alertmanager-rules.yaml` + `charts/lolday/tests/monitoring_alertrules_test.yaml`        | ⬜     |
| D   | `docs: sync Discord docs — HEARTBEAT env, alert count, deploy.sh channel names`                    | docs only                                            | `docs/architecture.md` + `.claude/rules/charts-and-helm.md` + `scripts/deploy.sh` (message strings)                         | ⬜     |
| E   | `docs(runbooks): add Discord webhook rotation runbook`                                             | new doc                                              | `docs/runbooks/discord-webhook-rotation.md` (new) + README + `docs/operations.md` (cross-link)                              | ⬜     |

⏭ Deferred (spec §10): promtool/amtool CI tests, kps default-rule
disablement, kps cause/effect inhibition expansion, notify success
metric.

## Per-PR detail

### PR A — `severity=info → null` route

**Branch:** `fix/charts-severity-info-null-route`

**Tasks:**

- [ ] Edit `charts/lolday/templates/monitoring/alertmanager-config-discord.yaml`:
      add an `info → null` route entry between the `none → null` route and
      the `critical` route. Include explanatory comment referencing
      spec §6.1 and the cosmetic-bug parallel to #206.
- [ ] Edit `charts/lolday/tests/alertmanagerconfig_test.yaml`:
      add `it: has a route with receiver null for severity=info (kps defaults)`
      test case mirroring the `none` case at lines 67–76.
- [ ] `helm lint charts/lolday`
- [ ] `helm template charts/lolday | grep -A 4 'severity'` — verify
      rendered route ordering.
- [ ] `pre-commit run --all-files` (must pass).
- [ ] Commit + push + open PR; wait CI green; `gh pr merge --squash`.

**Live verification:**

```bash
# (1) Apply the upgraded chart on server30:
bash scripts/deploy.sh

# (2) Wait ≤ 60 s for Alertmanager reload (kps watches the
# AlertmanagerConfig CR via the operator):
kubectl -n monitoring logs alertmanager-kps-alertmanager-0 -c alertmanager --tail=20 | grep -i 'reload\|configuration loaded'

# (3) Confirm the route mapping with amtool inside the AM pod:
kubectl -n monitoring exec alertmanager-kps-alertmanager-0 -c alertmanager -- \
  amtool config routes test \
  --config.file=/etc/alertmanager/config/alertmanager.yaml \
  severity=info
# Expected output: '{}/{}/{severity="info"}' → receiver "null"

# (4) After 60 min, fetch_messages on Spidey Warnings and confirm no
# `[WARNING] CPUThrottlingHigh` / `[WARNING] KubeQuota*` / `[WARNING]
# KubeNode*` entries since the deploy.
```

### PR B — drop `servicemonitor-volcano.yaml`

**Branch:** `fix/charts-drop-volcano-scheduler-servicemonitor`

**Tasks:**

- [ ] `git rm charts/lolday/templates/monitoring/servicemonitor-volcano.yaml`
- [ ] Edit `.claude/rules/charts-and-helm.md`: remove `volcano` from
      the `servicemonitor-{backend,dcgm,postgres,traefik,trivy,volcano}.yaml`
      list near §`templates/monitoring/` (verify exact wording before edit).
- [ ] Edit the same file's "six ServiceMonitor resources" wording →
      "five ServiceMonitor resources".
- [ ] `helm lint charts/lolday`
- [ ] `helm template charts/lolday | grep -A 2 'kind: ServiceMonitor'` —
      verify volcano-scheduler is absent and the other five remain.
- [ ] `pre-commit run --all-files`
- [ ] Commit + push + open PR; CI green; squash merge.

**Live verification:**

```bash
# (1) Apply:
bash scripts/deploy.sh

# (2) Verify the ServiceMonitor is gone:
kubectl get servicemonitor -n monitoring volcano-scheduler 2>&1 | grep -i 'NotFound'

# (3) Verify Prometheus drops the target (allow up to 30 s for the
# Prometheus pod to re-pick up its ConfigMap):
kubectl -n monitoring port-forward svc/kps-prometheus 9090:9090 &
PID=$!; sleep 3
curl -s 'http://localhost:9090/api/v1/targets?state=active' | \
  python3 -c "import json,sys; d=json.load(sys.stdin); \
  print('lolday-scheduler-service still listed:', \
  any('lolday-scheduler-service' in t.get('labels',{}).get('job','') \
  for t in d['data']['activeTargets']))"
kill $PID

# (4) Wait ≥ 10 min for the TargetDown 10m for-window to clear:
curl -s 'http://localhost:9090/api/v1/query' --data-urlencode \
  'query=ALERTS{alertname="TargetDown",alertstate="firing"}' | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['data']['result'])"
# Expected: [] (or only firings on jobs other than lolday-scheduler-service).
```

### PR C — `LoldayDiscordNotifyDropped` alert

**Branch:** `feat/monitoring-discord-notify-dropped-alert`

**Tasks:**

- [ ] Edit `charts/lolday/templates/monitoring/alertmanager-rules.yaml`:
      insert the new alert rule right after `LoldayDiscordNotifyFailing`
      in the `lolday-baseline.rules` group. Mirror its annotation style;
      use the spec §6.3 PromQL + threshold + `for: 10m`.
- [ ] Edit `charts/lolday/tests/monitoring_alertrules_test.yaml`:
      add `it: lolday-baseline.rules group contains LoldayDiscordNotifyDropped`
      test. Use `matchRegex` over `spec.groups[0].rules[*].alert` or pin
      by index (verify final position with `helm template`).
- [ ] `helm lint charts/lolday`
- [ ] (Optional but recommended) `helm template charts/lolday | promtool check rules /dev/stdin`
      — confirm the PromQL parses cleanly.
- [ ] `pre-commit run --all-files`
- [ ] Commit + push + open PR; CI green; squash merge.

**Live verification:**

```bash
# (1) Apply:
bash scripts/deploy.sh

# (2) Confirm the new rule is loaded:
kubectl -n monitoring port-forward svc/kps-prometheus 9090:9090 &
PID=$!; sleep 3
curl -s 'http://localhost:9090/api/v1/rules?type=alert' | \
  python3 -c "import json,sys; rules=[r for g in \
  json.load(sys.stdin)['data']['groups'] for r in g['rules'] \
  if r.get('name')=='LoldayDiscordNotifyDropped']; \
  print('loaded:', bool(rules)); print(rules[0] if rules else '')"
kill $PID
# Expected: loaded: True, threshold > 0.05, for: 10m, severity: warning
```

### PR D — docs sync (3 files)

**Branch:** `docs/discord-pipeline-sync`

**Tasks:**

- [ ] `docs/architecture.md` — add `DISCORD_WEBHOOK_URL_HEARTBEAT`
      to the §Discord env-var list (line 247) AND to the
      `.lolday-secrets.env` required-key list in §5.2 secrets table
      (line 255). Note `optional: true` semantics (Spidey Heartbeat
      degrades to failure-only when unset).
- [ ] `.claude/rules/charts-and-helm.md` — find the "16 alert rules
      total (alerting redesign 2026-05-10)" line; update to "20 alert
      rules total (alerting redesign 2026-05-10 + security-hardening
      P5/P6 + 2026-05-17 audit)". Verify the count by `grep -c '^        - alert:'
charts/lolday/templates/monitoring/alertmanager-rules.yaml` after
      PR C lands.
- [ ] `scripts/deploy.sh` — search for `#lolday-alerts-critical /
#lolday-alerts-warning / #lolday-alerts-events` strings (lines 23,
      24, 31); replace with `Captain Hook / Spidey Warnings /
      Spidey Service Alerts`. Cosmetic only; do not alter logic.
- [ ] `pre-commit run --all-files`
- [ ] Commit + push + open PR; CI green; squash merge.

**Live verification:**

- Render-only PR. After merge: `bash scripts/deploy.sh` to verify
  the channel-name messages match `docs/operations.md` §Discord
  channels.

**Note:** the initial draft of this plan included a
`docs/operations.md` debug-command fix. Live verification on K3s
1.34 showed the current command (`-l app.kubernetes.io/name=deadmans-switch`)
works as documented (Job inherits pod-template labels). Spec §5.5
amended; that line item dropped from this PR.

### PR E — `docs/runbooks/discord-webhook-rotation.md`

**Branch:** `docs/runbooks-discord-webhook-rotation`

**Tasks:**

- [ ] Create `docs/runbooks/discord-webhook-rotation.md`. Outline per
      spec §6.5; sections: Why rotate; Cadence; Per-channel UI procedure;
      Apply (`.lolday-secrets.env` → `deploy.sh` → `kubectl rollout
restart`); Verify (`amtool config check` + test alert); Backup
      (re-run operator-workstation-backup); Emergency rotation.
- [ ] Cross-link from `docs/operations.md` §Discord channels — one
      sentence in the "Webhook env mapping" preamble.
- [ ] Cross-link from `README.md` "Runbooks for specific operations"
      bulleted list.
- [ ] `pre-commit run --all-files`
- [ ] Commit + push + open PR; CI green; squash merge.

**Live verification:**

- Procedural doc only; no live verify until the next scheduled
  rotation cycle. Recommended: schedule a low-risk rotation of
  `DISCORD_WEBHOOK_URL_HEARTBEAT` (least impact — heartbeat is
  swallow-on-flake; absence ≤ 5 min during rotation is acceptable)
  as the first test pass.

## Cross-PR notes

- **Chart version**: All five PRs are template-only or docs-only.
  Per `.claude/rules/charts-and-helm.md` precedent (#181, #201, #205,
  #206), **do not bump `Chart.yaml.version` / `appVersion`** and do
  not touch `values.yaml` image tags.
- **CI**: each PR must pass `helm lint`, `helm template`,
  `helm unittest charts/lolday`, `pre-commit`, plus the standard
  GHA matrix (`lint.yml`, `helm.yml`).
- **Operator hand-off**: zero sudo steps. All `kubectl` /
  `helm upgrade --reset-then-reuse-values` runnable by the
  non-sudo operator account on server30.

## Sequencing rationale

PR A first (highest user-visible impact: stops `[WARNING]` mis-rendered
info-level alerts in Spidey Warnings). PR B next (removes the chronic
TargetDown floor in Spidey Warnings). PR C third (extends observability
without changing surface). PR D fourth (docs accumulate as A–C land;
captures all the renames in one pass). PR E last (independent;
runbook can land any time but landing it last lets the spec/plan
artefacts reference it).
