# Operations quick reference

Day-to-day operator data that needs to be in every Claude session: Discord
channel directory, `.env` file inventory, server access entry points.
Imported into the project root `CLAUDE.md` via `@docs/operations.md` so it
loads automatically at session start.

**Single-source-of-truth note** — Discord channel ID + behaviour live ONLY in
this file (no other doc duplicates the mapping). Env file **keys** are
canonical in `.lolday-secrets.env.example` + `docs/architecture.md` §5.2;
this file only describes which files exist and what each is for.

## Discord channels

Lolday uses four Discord group channels. Channel IDs come from
`~/.claude/channels/discord/access.json` `groups` key. Webhook URLs are
filled by the operator into `~/.lolday-secrets.env` and consumed via Helm.

| Channel name          | Channel ID            | Source                                                                                                                                  | Behaviour                                                                 |
| --------------------- | --------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Captain Hook          | `1495778266907279410` | Alertmanager `severity=critical` (alerting redesign 2026-05-10) + `deadmans-switch` Watchdog-fail pings                                 | `@here` ping; messages always require immediate action                    |
| Spidey Warnings       | `1502975656252670173` | Alertmanager `severity=warning` (added in alerting redesign 2026-05-10)                                                                 | No `@here`; FYI-only                                                      |
| Spidey Heartbeat      | `1495780321239502919` | `deadmans-switch` CronJob positive-heartbeat (every 5 min on Watchdog-fresh success; wired 2026-05-16 via §10 #31 follow-up to PR #201) | Messages mean healthy; absence is the anomaly                             |
| Spidey Service Alerts | `1495967957992603788` | backend Discord notify (`backend/app/services/discord.py` + `notify.py`)                                                                | Events targeted at specific users (`@bolin8017` / `@service-<id>.access`) |

Webhook env mapping (repo-root `.lolday-secrets.env`, fallback `~/.lolday-secrets.env`):

| Env var                         | Channel               | Current consumer                                                  |
| ------------------------------- | --------------------- | ----------------------------------------------------------------- |
| `DISCORD_WEBHOOK_URL_CRITICAL`  | Captain Hook          | Alertmanager `severity=critical` + `deadmans-switch` failure POST |
| `DISCORD_WEBHOOK_URL_WARNING`   | Spidey Warnings       | Alertmanager `severity=warning`                                   |
| `DISCORD_WEBHOOK_URL_EVENTS`    | Spidey Service Alerts | backend `services/discord.py`                                     |
| `DISCORD_WEBHOOK_URL_HEARTBEAT` | Spidey Heartbeat      | `deadmans-switch` CronJob — positive heartbeat on success         |

The chart wires the critical + heartbeat webhooks into the same `monitoring/alertmanager-discord` Secret under keys `webhook-url-critical` and `webhook-url-heartbeat`; the deadmans-switch CronJob env binding for the heartbeat key is `optional: true` so an empty/unset `DISCORD_WEBHOOK_URL_HEARTBEAT` gracefully degrades to "failure-only" (Captain Hook keeps working). The warning channel is consumed by Alertmanager via its own `webhook-url-warning` Secret key.

**Webhook rotation.** Webhook URLs are credentials (anyone with the URL can POST as the channel). Rotate quarterly as preventive hygiene, or immediately on suspected leak — operator procedure in [`docs/runbooks/discord-webhook-rotation.md`](runbooks/discord-webhook-rotation.md).

Debug entry points:

- Captain Hook `@here` surge → `kubectl -n monitoring port-forward svc/kps-prometheus 9090`, then `curl 'http://localhost:9090/api/v1/query?query=count by (alertname,severity) (count_over_time(ALERTS{alertstate="firing"}[7d]))'`. Also check `kubectl -n monitoring logs job/$(kubectl -n monitoring get jobs -l app.kubernetes.io/name=deadmans-switch -o name | tail -1)` for `Alertmanager unreachable` — recurrent on monitoring-ns NetworkPolicy regressions (precedent: 2026-05-16 hotfix, `docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md`).
- Spidey Warnings spamming many similar alerts → inhibit rule failed; `amtool config show` and compare against the 5 `inhibitRules` in spec `2026-05-10-alerting-redesign-design.md` §6.2
- Spidey Heartbeat silent (no green message in the last 10–15 min) → start with `kubectl -n monitoring get cronjob deadmans-switch` (suspended? last successful?). Then inspect the most recent Job's logs: `kubectl -n monitoring logs job/$(kubectl -n monitoring get jobs -l app.kubernetes.io/name=deadmans-switch -o name | tail -1)`. `Alertmanager unreachable` indicates a monitoring-ns NP regression (see Captain Hook entry above). `Positive heartbeat POST failed (non-fatal)` is a Discord-side rate-limit / transient flake — the next run typically recovers. `not set — positive heartbeat will be skipped` means the operator hasn't put `DISCORD_WEBHOOK_URL_HEARTBEAT` in `.lolday-secrets.env` and re-run `scripts/deploy.sh`.
- Service alert embed content unclear → grep `backend/app/services/discord.py` for the matching embed builder

History notes:

- The 2026-05-10 alerting redesign split Alertmanager traffic into two channels (critical → Captain Hook with `@here`, warning → Spidey Warnings without ping), keeping the critical channel clean. Spidey Warnings doesn't carry the "Bot" prefix, so naming isn't fully consistent; to align, rename the Discord channel (no impact on webhook URL / routing).

## Env / secrets files

Canonical inventory + per-file full key list: `docs/architecture.md` §5.2 +
template `.lolday-secrets.env.example`. This section only describes which
files exist at the repo root and what each is for.

- **`.lolday-secrets.env`** (gitignored, chmod 600) — main operator secrets file. Sourced by every script under `scripts/` that needs secrets via the canonical loader pattern in `scripts/recover-harbor.sh`.
- **`.lolday-cf-svctoken.env`** — operator-local split of the CF Access service-token credentials (`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`), kept apart for separate rotation cadence (per-migration cycle). Sourced manually for `/users/me` svctoken debug. Not listed in `architecture.md` §5.2 because it is operator-local, not platform-required.
- **`.lolday-cloudflare-access-backups/`** — directory of age-encrypted (`.json.age`) snapshots of CF Access app/policy state (audit). Encrypt with `age -r "$AGE_RECIPIENT" < state.json > state.json.age` per [`docs/runbooks/cf-access-backups.md`](runbooks/cf-access-backups.md); cleartext `.json` is forbidden. Not consumed by any script.
- **`frontend/.env.example`** — Vite dev env template. Production frontend image reads only build-time env, so a runtime `.env` does nothing.

Runtime cluster secrets are wired via `charts/lolday/templates/*-secret.yaml`,
filled out-of-band by the operator into K8s `Secret` objects — **not** mounted
from the files above. See `docs/runbooks/deploy.md` for the wiring.

## NFS dataset sources

Detector samples are **not** stored on server30's local SSD. They live on an
NFS share exported by server14 and are combined into a single
`samples_root` view via mergerfs.

> Real internal IPs (server30 host, server14 NFS export) live in
> `~/.lolday-secrets.env` (`SERVER30_IP`, `SERVER14_IP`); this doc uses
> placeholders so it can be public-flipped without leaking lab topology.

| Path on server30        | Backing                                                                   |
| ----------------------- | ------------------------------------------------------------------------- |
| `/mnt/server14/dataset` | NFSv4.2 from `<SERVER14_IP>:/mnt/hdd4t/dataset` (ro, `nofail`, `_netdev`) |
| `/mnt/lolday-samples`   | mergerfs union — branches ordered: 2025 → 2024 → benignware               |

The chart `samples.hostPath` (`charts/lolday/values.yaml`) points at
`/mnt/lolday-samples`. Detector vcjob pods mount the resulting PVC at
`/mnt/samples` (`SAMPLES_ROOT`). The backend pod itself does NOT mount
the samples PVC by design (`spot_check_samples` is best-effort, skipped
when the local path doesn't exist).

**Branch order = dedup priority.** A SHA-256 present in multiple banks
resolves to the file from the first matching branch (2025 wins over 2024
wins over benignware). Verifiable via:

```bash
python3 -c "import os; print(os.getxattr('/mnt/lolday-samples/<prefix>/<sha256>', 'user.mergerfs.fullpath').decode())"
```

**Adding / removing a dataset bank** — see `docs/runbooks/add-nfs-dataset.md`.
Short version:

1. Mount new NFS source at `/mnt/<src>` (ro, `nofail`, `_netdev`)
2. Edit `/etc/fstab` mergerfs line — insert path at desired priority
   position (leftmost = highest priority)
3. `sudo systemctl daemon-reload && sudo umount /mnt/lolday-samples && sudo mount -a`
4. Upload the bank's CSV as a fresh lolday `DatasetConfig`

No chart or backend change required.

**Cosmetic note**: server30 displays NFS file group as `diskaccess` after
2026-05-12 GID alignment (`diskaccess` was on GID 1001 locally, NFS sends
raw GID 1002 = server14's `diskaccess`). Pre-alignment displays showed
`campbell` (user owning GID 1002 locally before the swap). This has no
functional impact — files are world-readable.

Spec: `docs/superpowers/specs/2026-05-12-nfs-dataset-union-mount-design.md`.

## Server access

- Primary host: **server30** (single-node K3s; no IPMI / out-of-band — broken SSH = physical recovery)
- SSH port: **9453** (not 22)
- Operator usually has no sudo (granted temporarily, then revoked)

Full SSH discipline rules: project root `CLAUDE.md` §SSH safety on server30 +
`.claude/rules/scripts-and-ops.md` §SSH discipline. The 2026-03-31 incident
that established the rule: `docs/postmortems/2026-03-31-cilium-ssh-incident.md`.
