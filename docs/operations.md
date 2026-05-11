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

| Channel name          | Channel ID            | Source                                                                                | Behaviour                                                  |
| --------------------- | --------------------- | ------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| Captain Hook          | `1495778266907279410` | Alertmanager `severity=critical` (議題 B redesign 2026-05-10)                         | `@here` ping; 訊息一律必須立即處理                         |
| Spidey Warnings       | `1502975656252670173` | Alertmanager `severity=warning` (議題 B 新增 2026-05-10)                              | 無 `@here`，FYI 性質                                       |
| Spidey Heartbeat      | `1495780321239502919` | `deadmans-switch` CronJob (`charts/lolday/templates/monitoring/deadmans-switch.yaml`) | 有訊息 = 健康；沒訊息才是異常                              |
| Spidey Service Alerts | `1495967957992603788` | backend Discord notify (`backend/app/services/discord.py` + `notify.py`)              | 給特定 user 的事件 (`@bolin8017` / `@service-<id>.access`) |

Webhook env mapping (`~/.lolday-secrets.env`):

| Env var                        | Channel               |
| ------------------------------ | --------------------- |
| `DISCORD_WEBHOOK_URL_CRITICAL` | Captain Hook          |
| `DISCORD_WEBHOOK_URL_WARNING`  | Spidey Warnings       |
| `DISCORD_WEBHOOK_URL_EVENTS`   | Spidey Service Alerts |
| `DISCORD_URL` (CronJob only)   | Spidey Heartbeat      |

Debug entry points:

- Captain Hook `@here` 暴衝 → `kubectl -n monitoring port-forward svc/kps-prometheus 9090` 後 `curl 'http://localhost:9090/api/v1/query?query=count by (alertname,severity) (count_over_time(ALERTS{alertstate="firing"}[7d]))'`
- Spidey Warnings 噴大量同類 alert → inhibit rule 失效；`amtool config show` 比對 spec `2026-05-10-alerting-redesign-design.md` §6.2 的 5 條 `inhibitRules`
- Spidey Heartbeat 斷掉 → `kubectl -n lolday get cronjob deadmans-switch`（suspended? last successful?）+ 確認 `DISCORD_URL` env 仍有效
- Service alert embed 內容不明 → grep `backend/app/services/discord.py` 找對應 embed builder

History notes:

- 2026-05-10 議題 B redesign 把 Alertmanager 流量拆成兩個 channel（critical → Captain Hook with `@here`、warning → Spidey Warnings no ping），讓 critical 頻道乾淨。Spidey Warnings 沒帶 "Bot" 前綴，命名不完全一致；要對齊就 rename Discord channel（不影響 webhook URL / routing）。

## Env / secrets files

Canonical inventory + per-file full key list: `docs/architecture.md` §5.2 +
template `.lolday-secrets.env.example`. This section only describes which
files exist at the repo root and what each is for.

- **`.lolday-secrets.env`** (gitignored, chmod 600) — main operator secrets file. Sourced by every script under `scripts/` that needs secrets via the canonical loader pattern in `scripts/recover-harbor.sh`.
- **`.lolday-cf-svctoken.env`** — operator-local split of the CF Access service-token credentials (`CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`), kept apart for separate rotation cadence (per-migration cycle). Sourced manually for `/users/me` svctoken debug. Not listed in `architecture.md` §5.2 because it is operator-local, not platform-required.
- **`.lolday-cloudflare-access-backups/`** — directory of JSON snapshots of CF Access app/policy state (audit). Not consumed by any script.
- **`frontend/.env.example`** — Vite dev env template. Production frontend image reads only build-time env, so a runtime `.env` does nothing.

Runtime cluster secrets are wired via `charts/lolday/templates/*-secret.yaml`,
filled out-of-band by the operator into K8s `Secret` objects — **not** mounted
from the files above. See `docs/runbooks/deploy.md` for the wiring.

## Server access

- Primary host: **server30** (single-node K3s; no IPMI / out-of-band — broken SSH = physical recovery)
- SSH port: **9453** (not 22)
- Operator usually has no sudo (granted temporarily, then revoked)

Full SSH discipline rules: project root `CLAUDE.md` §SSH safety on server30 +
`.claude/rules/scripts-and-ops.md` §SSH discipline. The 2026-03-31 incident
that established the rule: `docs/postmortems/2026-03-31-cilium-ssh-incident.md`.
