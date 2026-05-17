# Discord Notification Audit — Design Specification

> **Built 2026-05-17.** This spec is the closure design for a full-stack audit
> of the lolday Discord notification system (Captain Hook / Spidey Warnings /
> Spidey Heartbeat / Spidey Service Alerts), Alertmanager routing, alert-rule
> quality, backend `services/notify.py` plumbing, webhook secret hygiene, and
> docs alignment. It picks up where `2026-05-10-alerting-redesign-design.md`
> left off and addresses gaps the post-redesign live traffic surfaced.

## 1. Overview

Six months of monitoring evolution (alerting redesign 2026-05-10, security
hardening P5/P6 2026-05-12, hotfix PRs #201–#206 2026-05-16) left lolday's
Discord pipeline in a working but **leaky** state. The 2026-05-17 audit
walked through ten scope areas (A–J in the engagement brief) against live
cluster + Discord channel evidence; the most consequential findings:

1. **Severity-routing leak** — kube-prometheus-stack ships 7 alert rules
   with `severity=info`. None of our routes match them; they fall through
   to the default `discord-warning` receiver and surface in **Spidey
   Warnings as `[WARNING] CPUThrottlingHigh`** etc. — the same rendering
   bug PR #206 closed for `severity=none` (`Watchdog` / `InfoInhibitor`).
2. **TargetDown chronic noise** — `lolday-scheduler-service:8080` has been
   `connection refused` for 10+ hours (and intermittently across days
   prior). Root cause: the Volcano sub-chart's scheduler `Deployment`
   does not declare `containerPort: 8080` and its container does not
   bind metrics on the port the `Service` advertises. The `ServiceMonitor`
   was added in Phase 7.3 with no consumer (`VolcanoJobsStuckPending` uses
   the **backend-side** `lolday_volcano_pending_stale` gauge, not the
   scheduler scrape).
3. **`discord_notify_dropped` has no dedicated alert** — the security-
   hardening P6 M-notify-semaphore lands a `BACKEND_ERRORS` stage when
   the `_NOTIFY_SEM=20` semaphore saturates and a user-targeted Discord
   notify is silently dropped. `LoldayBackendErrorRateElevated` catches
   _any_ non-zero stage but cannot distinguish "single transient httpx
   exception" from "user pings being dropped in bulk" — symptom-grade
   alerting requires a dedicated rule.
4. **Webhook rotation runbook is missing** — Cloudflare Access tokens
   have `docs/runbooks/p3-fernet-rotation.md`; Discord webhooks do not.
   The webhook URL **is** the credential (anyone with it can POST as
   any of the 4 channels), and `services/notify.py:73-79` correctly
   treats it as such, but there is no documented rotation cadence /
   procedure for the operator.
5. **`docs/operations.md` debug command is broken** — the rendered
   command for the "Captain Hook @here surge" entry uses
   `-l app.kubernetes.io/name=deadmans-switch` on the `Job` selector,
   but `lolday.labels` helper sets `app.kubernetes.io/name: lolday`
   on Jobs (the pod template gets the `deadmans-switch` label). So
   the documented command returns nothing, silently misdirecting an
   on-call operator chasing a real outage.
6. **Smaller drifts** — `.claude/rules/charts-and-helm.md` says "16
   alert rules" (actual count: 19, post security-hardening P5/P6);
   `docs/architecture.md` §5.2 missing `DISCORD_WEBHOOK_URL_HEARTBEAT`
   from the canonical env-var list; `scripts/deploy.sh` operator
   messages still reference pre-2026-05-10 channel names.

This spec is the design for closing all six in a series of mini-PRs
(precedent: today's #201–#206 cadence).

## 2. Authorization

User (engagement brief, 2026-05-17):

- **Breaking changes OK** — naming, severity reclassification, receiver
  black-listing all fair game.
- **Mainstream patterns first** — Google SRE Workbook, kube-prometheus-
  stack defaults, Prometheus / Alertmanager upstream docs. Deviations
  named explicitly.
- **Mini-PR cadence** — one PR per concern (template-only hotfixes
  ship without Chart.yaml bump per the post-#181 / #201 / #205 / #206
  precedent).
- **No SSH / sudo / Discord reply** during execution; live verification
  via `kubectl` + Prometheus port-forward + `amtool` + `fetch_messages`.

## 3. Scope

### 3.1 In scope

1. **Route fix: `severity=info → null`** — sister to PR #206 for
   `severity=none`. Adds one route entry + one helm-unittest case.
2. **TargetDown noise fix: remove `servicemonitor-volcano.yaml`** — no
   dashboard consumer; root cause is an upstream Volcano sub-chart
   defect (no `containerPort: 8080`); we should not paper over it by
   accepting permanent `connection refused`.
3. **New alert: `LoldayDiscordNotifyDropped`** — keyed off
   `BACKEND_ERRORS{stage="discord_notify_dropped"}`, rate-based, 10m
   `for:` window, severity `warning`. Mirrors the existing
   `LoldayDiscordNotifyFailing` rule but for the semaphore-saturation
   pathway (which has a different operator response — capacity / scale
   the backend, not rotate a webhook URL).
4. **Docs sync** — `docs/architecture.md` §5.2 +
   `.claude/rules/charts-and-helm.md` alert count +
   `docs/operations.md` deadmans-switch debug command +
   `scripts/deploy.sh` channel name strings. One PR.
5. **New runbook: `docs/runbooks/discord-webhook-rotation.md`** —
   per-channel rotation, emergency rotation, cadence
   recommendation. Modelled on `p3-fernet-rotation.md`.

### 3.2 Out of scope (explicit)

- **Promtool unit tests + amtool routing tests in CI** — the original
  2026-05-10 spec called for these (§8.1, §8.2); they were never
  delivered. Adding them is a larger change (new CI job, new test
  fixtures); deferred to a follow-up under the test-architecture
  programme (`docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md`).
- **Disable noisy kps default rules** (`KubeDeploymentRolloutStuck`,
  `KubeDaemonSetRolloutStuck`, …) — these did fire transiently
  during recent deploys but at low rates. Without 30+ days of
  baseline data we cannot confidently say which are noise vs signal;
  defer to a §10 tech-debt entry that the operator can revisit in
  Phase 8.
- **Cause/effect inhibition for kps overlaps** — e.g.
  `LoldayCoreServiceDown` could inhibit kps `KubePodCrashLooping` /
  `TargetDown` for the same `job=`. Defer because:
  (a) the current 5 inhibitions cover the lolday-internal cascade;
  (b) with `equal: [namespace]` semantics we would need careful
  label alignment to avoid masking unrelated crash-loops; (c) once
  `severity=info → null` lands and `volcano-scheduler` ServiceMonitor
  is removed, the dominant noise sources go away — the remaining
  kps warnings are low-volume.
- **Backend `_NOTIFY_SEM` size justification doc** — the inline
  comment in `services/notify.py:35-41` already cites the math
  (20 × 2 replicas = 40, vs httpx 100 and Discord 30/60s); no
  evidence the number is wrong; defer.
- **deadmans-switch debounce on extended outage** — 8 h of @here
  every 5 min during today's PR-#201-era NP gap is **intentional
  SRE behaviour** (dead-man-switch must keep paging until acked).
  Mainstream SRE consensus (Google SRE Book Ch. 9 "Simplicity":
  prefer fewer states; dead-man-switch is a stateless cron). Not
  changing.

### 3.3 Authorisation for breaking changes (recap)

§2 covers; this spec does not change:

- The 4-channel structure (Captain Hook / Spidey Warnings / Spidey
  Heartbeat / Spidey Service Alerts) — names and IDs unchanged.
- `services/discord.py` embed shape or `notify_*` call surface.
- The `deadmans-switch` CronJob behaviour (DISCORD_URL fail-fast,
  DISCORD_HEARTBEAT_URL swallow-on-flake — preserved).
- The 5 existing `inhibitRules` in `alertmanager-config-discord.yaml`.
- The 19 existing alert rules in `alertmanager-rules.yaml` (the new
  `LoldayDiscordNotifyDropped` is additive, taking the count to 20).

## 4. Background — what was on the air

### 4.1 Live evidence

Captured 2026-05-17 from server30 K3s via kubectl port-forward.

| Symptom                                                         | Evidence                                                                                                             | Root cause                                                                                                                                                                                                   |
| --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Captain Hook 8 h × 5-min `@here` spam                           | fetch_messages 2026-05-16 06:10 – 14:58 (96 messages, off-by-5min cadence matching `*/5 * * * *` CronJob)            | NP gap → deadmans-switch fails → fail-page POSTs to Captain Hook. Resolved by #201; behaviour itself is correct (page until acked).                                                                          |
| `TargetDown` warning firing on `lolday-scheduler-service` 10+ h | Prometheus `/api/v1/targets`: `health=down lastError="dial tcp 10.42.0.204:8080: connect: connection refused"`       | Volcano sub-chart 1.14.1 declares `--enable-metrics=true` in scheduler args but no `containerPort: 8080`; pod is not actually binding the port. ServiceMonitor scrapes a non-existent endpoint indefinitely. |
| `[WARNING] CPUThrottlingHigh` showing up in Spidey Warnings     | Prometheus 7-day count: `CPUThrottlingHigh` severity=`info` fired 2×; `KubeQuotaAlmostFull` severity=`info` fired 1× | Severity-routing leak; same shape as the #206 fix for `severity=none`.                                                                                                                                       |
| `Spidey Service Alerts` quiet since 2026-05-14                  | fetch_messages: last event 2026-05-14T15:45                                                                          | No user-driven jobs in the last 60 h; backend `LoldayDiscordNotifyFailing` not firing → not a regression in the notify pipeline. **OK; no action.**                                                          |
| Past Discord `429` storms (2026-05-08)                          | Alertmanager logs: 40 × `unexpected status code 429: rate_limited code 40062` clustered 2026-05-08T19:44–20:54       | Historical, predates the alerting redesign. Modern (post-2026-05-10) logs show only 2 errors, both from 2026-05-16 (`excessive retries creating aggregation group`) during PR-#201-era outage.               |

### 4.2 Alerting-surface inventory

Prometheus has **150 alert rules loaded** (37 critical / 104 warning /
7 info / 2 none). Only 19 of these are ours (`lolday-baseline`); 131
are kube-prometheus-stack defaults. The original 2026-05-10 redesign
audited the 19 lolday rules but did not document a routing decision
for the 7 `severity=info` kps defaults — they have been quietly
mis-rendering in Spidey Warnings ever since.

| severity   | count           | source          | route today                    | route after PR A |
| ---------- | --------------- | --------------- | ------------------------------ | ---------------- |
| `critical` | 37 (3 lolday)   | mix             | `discord-critical` (@here)     | unchanged        |
| `warning`  | 104 (13 lolday) | mix             | `discord-warning`              | unchanged        |
| `info`     | 7 (0 lolday)    | all kps default | **leaks to `discord-warning`** | `null`           |
| `none`     | 2 (0 lolday)    | all kps default | `null` (#206)                  | unchanged        |

### 4.3 Backend notify pipeline summary

- `services/discord.py` builds embed dicts (pure); `services/notify.py`
  delivers HTTP via httpx with 5 s timeout, swallows + counts errors.
- 5 `notify_*` call sites under `backend/app/reconciler/{jobs.py,builds.py,build_finalize.py,notify.py}`,
  all wrapped in `asyncio.create_task` (`# noqa: RUF006`).
- Service-token jobs return `NotifyContext = None` from
  `_user_context()` (`reconciler/notify.py:55-56`) so every notify call
  site early-returns — Phase 12 design preserved.
- `_NOTIFY_SEM = asyncio.Semaphore(20)` per pod; non-blocking acquire;
  saturation → `BACKEND_ERRORS{stage="discord_notify_dropped"}` + WARN
  log + drop. **No dedicated alert** — fixed by PR C in this spec.

## 5. Architecture decisions

### 5.1 Why route `severity=info` to `null`, not to a 3rd Discord receiver

ISLab scale (1 admin + ~10 users) cannot productively consume a 5th
channel. The 7 kps info-level alerts overlap with our explicit
critical rules:

| kps `severity=info` rule                     | covered by lolday `severity=critical` rule                              | overlap basis                           |
| -------------------------------------------- | ----------------------------------------------------------------------- | --------------------------------------- |
| `KubeNodePressure`                           | `LoldayNodeMemoryPressure` + `LoldayNodeDiskPressure`                   | both watch `kube_node_status_condition` |
| `KubeNodeEviction`                           | `LoldayNodeMemoryPressure` (eviction-soft trips MemoryPressure first)   |                                         |
| `KubeletTooManyPods`                         | (no lolday equivalent; defer — single-node K3s, never near pod limit)   |                                         |
| `CPUThrottlingHigh`                          | (telemetry, not a fault)                                                |                                         |
| `KubeQuotaAlmostFull` / `KubeQuotaFullyUsed` | `LoldayJobsQuotaMemoryNearLimit` + `LoldayJobsQuotaCPUNearLimit` at 85% |                                         |
| `NodeCPUHighUsage`                           | (telemetry; node-exporter generic)                                      |                                         |

Grafana DCGM + node-exporter dashboards already surface the
telemetry. Routing `info → null` drops the noise _and_ removes the
rendering bug. Mainstream pattern: kps explicitly ships `severity=info`
as "not for paging" (kube-prometheus-stack v0.84.0 changelog notes
the severity hierarchy: `critical | warning | info | none`); routing
`info` to a sink is the canonical filter.

### 5.2 Why remove `servicemonitor-volcano.yaml` (not fix the chart)

Three reasons:

1. **No consumer** — `VolcanoJobsStuckPending` reads
   `lolday_volcano_pending_stale` (backend-side gauge); no dashboard
   panel or alert references `volcano_scheduler_*` series.
2. **Root cause is upstream** — Volcano chart 1.14.1 ships the
   scheduler `Deployment` without a `containerPort` for the metrics
   port. Fixing this in-tree means patching a `*.tgz` sub-chart,
   which `.claude/rules/charts-and-helm.md` lists as the workflow's
   forbidden path (we should not commit `*.tgz` modifications).
3. **Negative value** — the broken scrape costs us a perpetually-
   firing `TargetDown` warning **and** the kps default `KubeletDown` /
   `PodCrashLooping` cascade can mistakenly attribute scheduler
   restarts as scheduler badness. Removing the ServiceMonitor removes
   the target from Prometheus entirely; `up` is undefined, `TargetDown`
   does not evaluate.

If Volcano scheduler metrics become useful later (e.g., a queue-depth
panel that does NOT duplicate `lolday_jobs_pending_total`), the spec
calls for re-introducing the ServiceMonitor _after_ the upstream
chart is fixed or a sidecar exposes the port — not before.

### 5.3 Why `LoldayDiscordNotifyDropped` is a separate alert, not a new `stage` on `LoldayDiscordNotifyFailing`

`LoldayDiscordNotifyFailing` is keyed off `stage="discord_notify"` —
that stage fires for httpx exceptions and non-2xx Discord responses.
The operator's response is to **rotate or unblock the webhook** (URL
revoked, server outage, Discord rate-limit storm).

`stage="discord_notify_dropped"` is a different failure mode: the
backend's _own_ per-pod semaphore saturated, no HTTP attempt was
made. The operator's response is to **scale or investigate burstiness**:
either bump `_NOTIFY_SEM` (currently 20 per pod), add a 3rd replica,
or trace which detector flow is firing N notifies in a tight window.

Separate alerts surface separate responses cleanly. Same severity
(`warning`), same channel (Spidey Warnings), same `for: 10m`
window, but distinct annotations and grouping. Sibling-Counter
pattern from `.claude/rules/backend.md` §`BACKEND_ERRORS` failure-bus
convention.

### 5.4 Why webhook rotation is a runbook, not automation

Discord webhook URLs are unsuitable for automated rotation because
the rotation requires a UI click in Discord's web app (no public API
for webhook regen). Same constraint applies to Slack incoming webhooks
and most chat-integration tokens. The mainstream pattern is an
operator runbook with a recommended cadence (per-program audit cycle,
or 90-day default), plus emergency rotation steps for "URL leaked"
incidents. Modelled directly on the Cloudflare Access service-token
runbook (`docs/runbooks/p3-fernet-rotation.md`).

### 5.5 Why `docs/operations.md` debug command must use a different selector

The deadmans-switch CronJob template applies `lolday.labels` to:

- `ConfigMap` (`deadmans-switch-script`) — gets `app.kubernetes.io/name: lolday`
- `CronJob` itself — gets `app.kubernetes.io/name: lolday`
- `Job` (auto-created by CronJob controller, inherits CronJob labels) — gets `app.kubernetes.io/name: lolday`
- **Pod** (`spec.jobTemplate.spec.template.metadata.labels`) — overridden to `app.kubernetes.io/name: deadmans-switch`

So `kubectl -n monitoring get jobs -l app.kubernetes.io/name=deadmans-switch`
returns nothing. The correct query is either:

- `kubectl -n monitoring get jobs --selector=batch.kubernetes.io/cronjob=deadmans-switch -o name | tail -1`
  (uses the upstream auto-label; works for `batch/v1` CronJob in K8s >=1.27)
- or `kubectl -n monitoring get pods -l app.kubernetes.io/name=deadmans-switch`
  then map back via owner reference. More verbose.

Fix uses the upstream auto-label — it is the canonical / stable selector.

## 6. Detailed design

### 6.1 PR A — `severity=info → null`

`charts/lolday/templates/monitoring/alertmanager-config-discord.yaml`:

```yaml
spec:
  route:
    receiver: discord-warning
    groupBy: [alertname, severity]
    groupWait: 30s
    groupInterval: 5m
    repeatInterval: 24h
    routes:
      # severity=none — Watchdog / InfoInhibitor (kps general.rules)
      - receiver: "null"
        matchers:
          - { name: severity, value: none, matchType: "=" }
      # severity=info — kps defaults that are non-actionable in lolday's
      # context (overlap with our explicit severity=critical rules:
      # KubeNodePressure / KubeNodeEviction ⊂ LoldayNodeMemoryPressure;
      # KubeQuotaAlmostFull / KubeQuotaFullyUsed ⊂ LoldayJobsQuota*;
      # CPUThrottlingHigh / NodeCPUHighUsage / KubeletTooManyPods are
      # telemetry, visible in Grafana, not paging-grade).
      # Without this route they fall through to discord-warning and
      # surface in Spidey Warnings as `[WARNING] CPUThrottlingHigh`,
      # the same rendering bug PR #206 closed for severity=none.
      - receiver: "null"
        matchers:
          - { name: severity, value: info, matchType: "=" }
      - receiver: discord-critical
        matchers:
          - { name: severity, value: critical, matchType: "=" }
        repeatInterval: 4h
      - receiver: discord-warning
        matchers:
          - { name: severity, value: warning, matchType: "=" }
        repeatInterval: 24h
```

`charts/lolday/tests/alertmanagerconfig_test.yaml` — add one test case:

```yaml
- it: has a route with receiver null for severity=info (kps defaults)
  asserts:
    - contains:
        path: spec.route.routes
        content:
          receiver: "null"
          matchers:
            - name: severity
              value: info
              matchType: "="
```

The default-receiver fall-through stays `discord-warning` (fail-loud
for any genuinely-unknown severity that lands later — operator sees
it surface in Spidey Warnings and adds a route in a follow-up). The
catch-all default itself is unchanged.

### 6.2 PR B — drop `servicemonitor-volcano.yaml`

Delete `charts/lolday/templates/monitoring/servicemonitor-volcano.yaml`
entirely (no replacement). Update `.claude/rules/charts-and-helm.md`
`servicemonitor-*` list to drop `volcano`. Update the in-template
comment audit (if any test file references it — verify before
deletion).

Post-deploy verification:

- `kubectl get servicemonitor -n monitoring volcano-scheduler` → NotFound.
- Prometheus `/api/v1/targets`: no entry for `job="lolday-scheduler-service"`.
- After ≥ 10 min: `TargetDown` warning clears.
- After ≥ 30 min: Spidey Warnings no longer receives `[WARNING] TargetDown`.

### 6.3 PR C — `LoldayDiscordNotifyDropped` alert

`charts/lolday/templates/monitoring/alertmanager-rules.yaml` append
to the `lolday-baseline.rules` group, right after
`LoldayDiscordNotifyFailing`:

```yaml
# M-notify-semaphore drop alert (security-hardening P6 closure).
# notify.py's _NOTIFY_SEM=20 saturates → notify is silently dropped
# → BACKEND_ERRORS{stage="discord_notify_dropped"} increments.
# User-targeted Spidey Service Alerts go missing. Separate alert
# from LoldayDiscordNotifyFailing because the operator response
# is "scale / investigate burstiness", not "rotate webhook".
- alert: LoldayDiscordNotifyDropped
  expr: rate(lolday_backend_errors_total{stage="discord_notify_dropped"}[10m]) > 0.05
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Discord notify dropped at semaphore ({{`{{ $value | humanize }}`}}/s for 10m)"
    description: "_NOTIFY_SEM (20 permits per pod) saturated; user-targeted Spidey Service Alerts missing. Check `kubectl -n lolday top pods -l app=backend` for burst load + count by-detector via `kubectl -n lolday logs deploy/backend | grep 'discord_notify_dropped'`. If sustained, consider raising the semaphore size (`backend/app/services/notify.py:_NOTIFY_SEM`) or scaling backend replicas (currently 2)."
```

Threshold `> 0.05/s` ≈ 30 drops in 10 min; `for: 10m` keeps a transient
2-replica concurrent burst from paging. Spec-aligned with
`LoldayDiscordNotifyFailing`'s `> 0.1/s` (looser there because the
underlying httpx exception is more transient than a semaphore drop).

`charts/lolday/tests/monitoring_alertrules_test.yaml` — add:

```yaml
- it: lolday-baseline.rules group contains LoldayDiscordNotifyDropped
  asserts:
    - matchRegex:
        path: "spec.groups[0].rules[*].alert"
        pattern: "LoldayDiscordNotifyDropped"
```

(or whatever the suite idioms are; final form determined in
implementation.)

### 6.4 PR D — docs sync (single PR, 4 files)

1. **`docs/architecture.md` §5.2** — add row for
   `DISCORD_WEBHOOK_URL_HEARTBEAT` to the canonical env-var table.
   Wired 2026-05-16 by PR #202; missed in the docs PR.
2. **`docs/operations.md` §Discord channels** — fix the two debug
   commands referencing `app.kubernetes.io/name=deadmans-switch` on
   the Job selector. Replace with the CronJob auto-label form
   `batch.kubernetes.io/cronjob=deadmans-switch`.
3. **`.claude/rules/charts-and-helm.md`** — "16 alert rules total"
   → "20 alert rules total" (post-PR C; was 19 pre-PR C since
   `LoldayAuthFailureSpike` + `LoldayRateLimitSpike` +
   `LoldayDiscordNotifyFailing` landed in security-hardening P5/P6
   but the line was not bumped).
4. **`scripts/deploy.sh`** — operator-facing messages that mention
   `#lolday-alerts-critical / #lolday-alerts-warning / #lolday-events`
   → Captain Hook / Spidey Warnings / Spidey Service Alerts. Cosmetic
   only; no behavioural change.

### 6.5 PR E — `docs/runbooks/discord-webhook-rotation.md`

New runbook, cross-linked from `docs/operations.md` §Discord channels
"Webhook rotation" line + README "Runbooks for specific operations"
list. Outline:

1. **Why rotate** — webhook URL is the credential; leaked URLs let
   anyone POST as the channel. No automated revocation.
2. **Cadence** — quarterly default; immediate on suspected leak.
3. **Per-channel procedure** — Discord UI → Server Settings →
   Integrations → Webhooks → select the bot → "Copy URL" or
   regenerate. (Note: regenerating in Discord invalidates the old
   URL atomically.)
4. **Apply** — update the relevant key in `~/.lolday-secrets.env`
   (`DISCORD_WEBHOOK_URL_CRITICAL` / `_WARNING` / `_HEARTBEAT` /
   `_EVENTS`), re-run `bash scripts/deploy.sh` (which re-creates the
   K8s `Secret`), then bounce Alertmanager pod for receiver pickup
   (`kubectl -n monitoring rollout restart statefulset/alertmanager-kps-alertmanager`).
5. **Verify** — `amtool config check`, then a deliberate test alert
   via `amtool alert add` for each new webhook.
6. **Backup the new URL** — re-run the operator-workstation backup
   (`docs/runbooks/operator-workstation-backup.md`).
7. **Emergency rotation** (leaked URL) — same steps minus the
   scheduling, plus a Discord-UI step to revoke before regenerating
   (prevents an attacker from out-racing the rotation).

## 7. Failure modes

| Mode                                                              | Trigger                                                                                                                                                                              | Impact                                                                                                | Handling                                                                                                |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| PR A landed but `info` route at wrong position                    | Helm template renders routes in declaration order; if `info → null` is added after `warning` and `info` happens to also carry `severity=warning` by accident, the warning route wins | None in practice — kps does not emit dual-severity labels; matcher is exact-match on a single label   | helm-unittest `contains` assertion catches; live `amtool config routes test severity=info` after deploy |
| PR B leaves stale `endpoints` object                              | Kubernetes does not GC `Endpoints` from a deleted ServiceMonitor (it GCs from a deleted Service, which we are not touching)                                                          | None — the upstream Volcano `Service` stays; Prometheus stops scraping when ServiceMonitor disappears | n/a                                                                                                     |
| PR C threshold `> 0.05/s` too tight                               | A genuine burst of 30 user actions in 10 min trips the warning                                                                                                                       | False positive in Spidey Warnings; no @here, no impact on critical path                               | Bump threshold after baseline observation (§10 follow-up); keep `for: 10m`                              |
| PR D `batch.kubernetes.io/cronjob` label unsupported on older K3s | The auto-label landed in K8s 1.27; server30 runs K3s ≥ 1.30                                                                                                                          | n/a — confirmed via `kubectl get jobs -n monitoring --show-labels`                                    | If the label is empty, fall back to `--selector=job-name` filter, but it should be present              |
| PR E runbook recommends regenerate-then-update                    | A regenerate in Discord UI invalidates the old URL immediately; if `deploy.sh` is not run within ~1 min, alerts may fail to deliver                                                  | At most one missed alert per channel during the rotation gap                                          | Runbook explicit: do not regenerate until ready to re-deploy                                            |

## 8. Testing strategy

### 8.1 helm-unittest (CI, every PR)

- PR A: new `it:` case asserting `info → null` route presence.
- PR B: existing tests unchanged; lint will catch any stray reference.
- PR C: new `it:` case asserting the alert name + severity + `for:`
  window. May need `matchRegex` over `spec.groups[0].rules[*].alert`
  since helm-unittest's JSONPath-lite does not support index-by-name.

### 8.2 Live verification (after each `helm upgrade`)

- **PR A** —
  - `kubectl -n monitoring exec alertmanager-kps-alertmanager-0 -c alertmanager -- amtool config routes test --config.file=/etc/alertmanager/config/alertmanager.yaml severity=info` → expect receiver `null`.
  - After 24 h: confirm no `[WARNING] CPUThrottlingHigh` /
    `[WARNING] KubeQuota*` / `[WARNING] KubeNode*` in Spidey
    Warnings via `fetch_messages`.
- **PR B** —
  - `kubectl get servicemonitor -n monitoring volcano-scheduler` → NotFound.
  - After 10 min: Prometheus `/api/v1/targets` no entry for
    `lolday-scheduler-service`; `TargetDown` warning clears.
- **PR C** —
  - Promtool expression check at lint time (helm template + promtool
    rules check).
  - Optional: synthesise a saturation with `kubectl exec deploy/backend -- python -c '...trigger 21 concurrent notify...'` and watch the rule transition to `pending` → `firing`. Defer if expensive.
- **PR D** —
  - Render-only check (no runtime behaviour). `helm template`
    - spot inspection.
- **PR E** —
  - Runbook is procedural; no live verify until next scheduled
    rotation.

### 8.3 Regression guards

- helm-unittest case for PR A is the long-term regression guard.
- `alertmanagerconfig_test.yaml` "5 inhibitRules" length assertion
  prevents accidental rule removal (unchanged this spec).
- `monitoring_alertrules_test.yaml` already pins
  `LoldayCoreServiceDown.for == 5m`; analogous pin on the new alert.

## 9. Rollback

Each PR is independently revertable:

- **PR A** — `git revert` removes the info route; alerts go back to
  leaking to Spidey Warnings. No impact on critical path.
- **PR B** — `git revert` restores `servicemonitor-volcano.yaml`;
  `TargetDown` resumes firing. No impact on critical path.
- **PR C** — `git revert` removes the alert rule; semaphore drops
  fall back to `LoldayBackendErrorRateElevated` (less specific). No
  notification-delivery impact.
- **PR D** — `git revert` reverts docs only.
- **PR E** — `git revert` removes the runbook; existing rotation
  knowledge in operator's head.

## 10. Open questions / future work

1. **Promtool + amtool tests in CI** — listed §3.2 OOS. Recommend a
   follow-up under the test-architecture programme: new CI job
   `monitoring-rules.yml` that runs `promtool test rules` on
   `tests/<spec>-promtool.yaml` fixtures + `amtool config routes
test` for the documented severity values.
2. **Suppress noisy kps default warnings** — `KubeDeploymentRolloutStuck`,
   `KubeDaemonSetRolloutStuck`, `KubeContainerWaiting` fired transiently
   during recent deploys. After 30 d of post-PR-A baseline,
   revisit whether to disable specific kps rules via
   `kps.defaultRules.disabled[]`.
3. **Inhibition expansion** — once severity=info → null and
   ServiceMonitor cleanup land, see whether residual cascade noise
   warrants kps cause/effect inhibitions (e.g.,
   `LoldayCoreServiceDown` inhibits `KubePodCrashLooping` for
   `equal: [namespace, pod]`).
4. **Backend Prometheus metric for notify success rate** — currently
   no `lolday_discord_notify_total{result=...}` Counter. Helpful for
   SLO tracking, not required for current alerting.
5. **Per-channel rate-limit metric** — `notify.py` could expose a
   per-host rate counter; would let us add a `LoldayDiscordWebhookRateLimit`
   alert. Low ROI today, defer.

## 11. References

### Mainstream practice

- Google SRE Workbook — [Ch. 5: Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)
- Google SRE Book — [Ch. 9: Simplicity](https://sre.google/sre-book/simplicity/) (dead-man-switch as stateless cron)
- Prometheus — [Alerting best practices](https://prometheus.io/docs/practices/alerting/)
- kube-prometheus-stack — [README: severity hierarchy](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
- Alertmanager — [Inhibit rules](https://prometheus.io/docs/alerting/latest/configuration/#inhibit_rule)
- Discord — [Webhook rate limits](https://discord.com/developers/docs/topics/rate-limits#rate-limits)
- amtool — [config routes test](https://github.com/prometheus/alertmanager/tree/main/cmd/amtool)

### Lolday internal

- Alerting redesign 2026-05-10 — `docs/superpowers/specs/2026-05-10-alerting-redesign-design.md`
- Recent hotfixes — PRs #201–#206 (2026-05-16, all in git log)
- Backend notify pattern — `.claude/rules/backend.md` §`Discord notify pattern`
- `BACKEND_ERRORS` failure-bus convention — `.claude/rules/backend.md` §`BACKEND_ERRORS failure-bus convention`
- Operations quick reference — `docs/operations.md` §Discord channels
- CF Access service-token rotation precedent — `docs/runbooks/p3-fernet-rotation.md`
