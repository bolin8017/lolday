# Discord webhook rotation — operator runbook

**Scope:** rotating any of lolday's four Discord webhook URLs:

| `.lolday-secrets.env` key       | Channel               | Consumer                                                      |
| ------------------------------- | --------------------- | ------------------------------------------------------------- |
| `DISCORD_WEBHOOK_URL_CRITICAL`  | Captain Hook          | Alertmanager critical receiver + deadmans-switch failure POST |
| `DISCORD_WEBHOOK_URL_WARNING`   | Spidey Warnings       | Alertmanager warning receiver                                 |
| `DISCORD_WEBHOOK_URL_EVENTS`    | Spidey Service Alerts | backend `services/notify.py` (user-targeted job/build events) |
| `DISCORD_WEBHOOK_URL_HEARTBEAT` | Spidey Heartbeat      | deadmans-switch positive-heartbeat POST (`optional: true`)    |

**Cadence:** quarterly (90 days) as preventive hygiene; **immediately**
on suspected leak. The webhook URL **is** the credential — anyone with
it can POST as the channel. `services/notify.py` already treats it as a
secret (logs only `host + status`, never the path / token); this runbook
extends that discipline to the rotation lifecycle.

**Why a runbook, not automation:** Discord does not expose a public API
to regenerate webhooks programmatically — rotation requires a UI click in
the Discord web app (Server Settings → Integrations → Webhooks). Same
constraint applies to Slack incoming webhooks and most chat-integration
tokens. Mainstream practice is an operator runbook + recommended cadence.
Modelled on [`docs/runbooks/p3-fernet-rotation.md`](p3-fernet-rotation.md).

## Pre-flight (T-10 min)

- [ ] Confirm operator workstation has the current `~/.lolday-secrets.env`
      backed up off-site (`docs/runbooks/operator-workstation-backup.md`).
- [ ] Confirm `helm upgrade --install lolday` is healthy:
      `kubectl -n lolday get pods` + `kubectl -n monitoring get pods` — all
      `Running`.
- [ ] Identify which channel(s) are being rotated. Note: rotating
      `DISCORD_WEBHOOK_URL_HEARTBEAT` has the lowest blast radius
      (heartbeat is swallow-on-flake; absence ≤ 5 min is acceptable) —
      good first-time exercise of this runbook.
- [ ] Have a Discord browser session open with `Manage Webhooks`
      permission on the lolday server.

## Procedure (per channel)

```text
# T-0 = rotation window start

# 1. In Discord UI: Server Settings → Integrations → Webhooks → select
#    the bot for the target channel (e.g., "Captain Hook") → click the
#    URL field's "Copy" icon to capture the CURRENT value, then click
#    "Reset URL" / regenerate. The OLD URL is invalidated immediately
#    when the new URL is generated.

#    IMPORTANT: do not regenerate until you are ready to re-deploy.
#    Between the regenerate click and the helm upgrade, Alertmanager /
#    backend will be posting to a URL Discord no longer accepts;
#    delivery will fail for up to ~60 s (one notify-attempt cycle).
#    For Captain Hook / Spidey Warnings: any active alert during this
#    gap will retry on the next Alertmanager group interval. For
#    Spidey Service Alerts: any user-event during this gap is lost
#    (fire-and-forget, no retry).

# 2. Copy the NEW URL out of the Discord UI into a clipboard buffer.

# 3. Update the operator workstation env file:
$EDITOR ~/.lolday-secrets.env
#    Find the relevant DISCORD_WEBHOOK_URL_* line, replace the value.
#    Verify chmod stays 600:
ls -l ~/.lolday-secrets.env
#    Expected: -rw------- ...

# 4. Re-deploy. `deploy.sh` re-creates the `monitoring/alertmanager-discord`
#    and `lolday/discord-events` Secrets from the env file.
bash scripts/deploy.sh

# 5. Restart the consumers so they pick up the new Secret value.
#    Alertmanager auto-reloads on AlertmanagerConfig CR changes but
#    NOT on Secret-only changes (kps StatefulSet does not watch
#    arbitrary Secret revisions). Trigger a rollout:
kubectl -n monitoring rollout restart statefulset/alertmanager-kps-alertmanager
kubectl -n monitoring rollout status  statefulset/alertmanager-kps-alertmanager
#    Backend reads DISCORD_WEBHOOK_URL_EVENTS at pod boot via Settings.
#    Rotate backend pods too if the EVENTS webhook was changed:
kubectl -n lolday rollout restart deploy/backend
kubectl -n lolday rollout status  deploy/backend
#    deadmans-switch CronJob picks up the new HEARTBEAT / CRITICAL URL
#    on the next */5min schedule — no restart needed.

# 6. Verify delivery. Fire a deliberate test alert for each rotated
#    channel:
kubectl -n monitoring exec alertmanager-kps-alertmanager-0 -c alertmanager -- \
  amtool alert add \
    --alertmanager.url=http://localhost:9093 \
    alertname=ManualWebhookRotationTest \
    severity=warning \
    summary='Discord webhook rotation smoke test — Spidey Warnings'
#    Switch alertname=, severity= per channel under test (severity=critical
#    for Captain Hook; severity=info / severity=none route to null, do NOT
#    fire to verify those — they are intentionally silenced).
#    Confirm the embed appears in the target Discord channel within ~30s.
#    Then resolve the test silence:
kubectl -n monitoring exec alertmanager-kps-alertmanager-0 -c alertmanager -- \
  amtool silence add --duration=2m alertname=ManualWebhookRotationTest

# 7. Smoke-test the backend EVENTS path (only if rotating EVENTS):
#    The cleanest path is to trigger a real notify event end-to-end.
#    Easiest test: submit a tiny detector job that completes in < 1 min
#    (per /api/v1/jobs runbook), then confirm the @-mention embed reaches
#    Spidey Service Alerts. There is no synthetic "trigger notify"
#    endpoint by design — the backend treats notify as fire-and-forget.

# 8. Backup the updated env file.
#    Re-run the operator-workstation backup so the new webhook value
#    survives a workstation loss:
bash scripts/operator-workstation-backup.sh  # or whatever the script is
#    See docs/runbooks/operator-workstation-backup.md.

# 9. Log the rotation in an operator log of your choice (commit message,
#    ops notebook, Discord pinned post). Record: channel, rotation
#    timestamp, reason (cadence / suspected leak / planned).
```

## Emergency rotation (suspected leaked URL)

When you suspect a URL has leaked — pasted in a public channel, found in
a public commit history, or unexpected POSTs are appearing in the target
channel — rotate **before** doing root-cause analysis. The cost of
rotation is ~5 min; the cost of an attacker continuing to POST is unbounded.

Differences from the routine procedure:

- **Step 1: regenerate without delay** — even before opening the env
  file. The compromised URL is invalidated by Discord at the moment the
  regenerate button is clicked.
- **Step 4: prioritise the re-deploy** — the gap window has real cost
  (Alertmanager will retry; users may briefly not receive notifies). The
  trade-off vs the cost of attacker continuation is heavily on the side
  of "rotate now".
- **Skip step 6 verification with `amtool alert add`** — the next real
  alert (or absence of alarm in Spidey Heartbeat after 5 min) is your
  verification.
- **Step 9 log entry is mandatory** — include the suspected leak vector
  and remediation. If the leak was via a `~/.lolday-secrets.env` file
  outside the operator workstation, audit the propagation path
  (`cf-access-backups` bundle? screenshare? laptop loss?) and consider
  rotating all four webhooks defensively.

## Failure modes

| Symptom                                                  | Likely cause                                                                                                                                                                                                                                        | Resolution                                                                                                                                                |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- | ---------------------------------------------------- |
| Test alert in step 6 does not arrive                     | Alertmanager pod did not pick up the new Secret (the rollout in step 5 was skipped or failed)                                                                                                                                                       | `kubectl -n monitoring describe pod alertmanager-kps-alertmanager-0` — check `ContainerCreating` / failed mount; re-run the rollout                       |
| Alertmanager logs `unexpected status code 401`           | The new URL value was pasted with surrounding whitespace, leading/trailing newline, or missing the `discord.com` prefix — the regex guard in `scripts/deploy.sh:45-56` catches well-formed-but-wrong-token, but does NOT catch a stale OLD-URL race | Re-copy from Discord UI, ensure no clipboard artefacts (`echo "$DISCORD_WEBHOOK_URL_CRITICAL"                                                             | xxd                                                           | head` — should end with newline after a single line) |
| Spidey Heartbeat goes silent for > 10 min after rotation | New URL is malformed / Discord-side regenerate did not propagate                                                                                                                                                                                    | Check deadmans-switch latest Job logs: `kubectl -n monitoring logs job/$(kubectl -n monitoring get jobs -l app.kubernetes.io/name=deadmans-switch -o name | tail -1)`; expect `Positive heartbeat POST failed`or`not set` |
| Captain Hook stops receiving real alerts                 | (Same as test-alert failure above) — but missed real alerts have higher cost. If unrecovered after the rollout, **temporarily** point `webhook-url-critical` back at a known-good URL (e.g. operator's personal channel) until root cause is found  | Use `kubectl -n monitoring edit secret alertmanager-discord` for the temp fix; rotate the real URL again after diagnosis                                  |

## Cross-references

- Channel directory + behaviour map: [`docs/operations.md` §Discord channels](../operations.md)
- Webhook env-var inventory: [`docs/architecture.md` §5.2](../architecture.md)
- Backend notify pipeline: [`backend/app/services/notify.py`](../../backend/app/services/notify.py) + `.claude/rules/backend.md` §`Discord notify pattern`
- deadmans-switch design: [`charts/lolday/files/deadmans_switch/check.py`](../../charts/lolday/files/deadmans_switch/check.py) + spec `2026-05-10-alerting-redesign-design.md`
- 2026-05-17 Discord notification audit: [`docs/superpowers/specs/2026-05-17-discord-notification-audit-design.md`](../superpowers/specs/2026-05-17-discord-notification-audit-design.md) §5.4 + §6.5
- Sister runbook (Fernet key rotation): [`docs/runbooks/p3-fernet-rotation.md`](p3-fernet-rotation.md)
- Workstation backup (back up `.lolday-secrets.env` after rotation): [`docs/runbooks/operator-workstation-backup.md`](operator-workstation-backup.md)
