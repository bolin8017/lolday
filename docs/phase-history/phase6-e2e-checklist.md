# Phase 6 E2E Checklist

> Parallel to `phase4-e2e-checklist.md`. Run top-to-bottom after each sub-phase deploy.

## Prerequisites

- [x] K3s `local-path` StorageClass exists, `/` has ≥ 60 Gi free <sub>2026-04-20: `/` 98 G total, 39 G free (59 % used). Below the 60 Gi target but not a blocker; revisit if monitoring retention grows.</sub>
- [ ] `~/.lolday-secrets.env` sources cleanly, contains `GRAFANA_ADMIN_PASSWORD`, `PG_EXPORTER_PASSWORD`, `CF_ENABLED`, `CF_TUNNEL_TOKEN` <sub>Operator-local; out of scope for automated verification.</sub>
- [x] `connlabai.com` is in Cloudflare DNS <sub>2026-04-20: `getent hosts lolday.connlabai.com` → Cloudflare anycast 2606:4700:\*</sub>
- [x] Cloudflare Tunnel `lolday-server30` exists <sub>2026-04-20: 4× "Registered tunnel connection" on each cloudflared pod (tpe01/hkg01/tpe01/hkg09).</sub>
- [x] Access Application `lolday` with the NTUST policy exists <sub>2026-04-20: anonymous `curl -I` 302 → `bolin8017.cloudflareaccess.com/cdn-cgi/access/login/lolday.connlabai.com`.</sub>

## Sub-phase 6-1 — Monitoring stack

Run: `bash scripts/phase6-pre-deploy-check.sh && bash scripts/deploy.sh`

- [x] Prometheus / Alertmanager / kps-operator pods Running in `monitoring` ns <sub>2026-04-20: 3/3 Running.</sub>
- [x] Grafana / Loki / Promtail / kube-state-metrics / prometheus-node-exporter pods Running in `lolday` ns (Grafana subchart ignores parent namespaceOverride, so these land in release ns) <sub>2026-04-20: all Running.</sub>
- [ ] Grafana reachable via `kubectl -n lolday port-forward svc/kps-grafana 3000:80` <sub>Not re-verified this session; confirmed at original Phase 6 deploy.</sub>
- [ ] Grafana login works with `admin` / `$GRAFANA_ADMIN_PASSWORD` <sub>Same as above.</sub>
- [ ] Dashboard "Kubernetes / Compute Resources / Cluster" has data <sub>Same as above.</sub>
- [ ] Dashboard "NVIDIA DCGM Exporter Dashboard" shows both GPUs <sub>Same as above.</sub>
- [ ] Dashboard "Traefik 3" shows request counters <sub>Same as above.</sub>
- [ ] Dashboard "PostgreSQL Database" shows connections <sub>Same as above.</sub>
- [ ] LogQL `{namespace="lolday"}` returns results (port-forward `svc/loki` in `lolday` ns on :3100, then hit `/loki/api/v1/query_range`) <sub>Same as above.</sub>
- [ ] Prometheus `/api/v1/targets` shows ≥ 10 up jobs (port-forward `svc/kps-prometheus` in `monitoring` ns on :9090, then `curl http://localhost:9090/api/v1/targets`) <sub>Same as above.</sub>
- [ ] Alertmanager UI (port-forward `svc/kps-alertmanager` in `monitoring` ns on :9093) lists 4 inactive baseline alerts <sub>Same as above.</sub>
- [ ] Phase 4 curl E2E passes <sub>Not re-run this session; last green at Phase 6 merge (commit 71506c1).</sub>
- [ ] Phase 5 Playwright E2E passes <sub>Not re-run this session; skipped per Phase 6 sign-off discussion.</sub>

## Sub-phase 6-2 — Access policy

- [x] Cloudflare → Zero Trust → Applications shows `lolday` with NTUST policy <sub>Implied by 302 redirect with `@mail.ntust.edu.tw`-gated login page.</sub>
- [x] `cloudflared access login https://lolday.connlabai.com` issues a token for an NTUST Google account <sub>Operator-confirmed in original Phase 6 deploy.</sub>
- [x] Same command with a non-NTUST account shows Access Denied <sub>Operator-confirmed in original Phase 6 deploy.</sub>

## Sub-phase 6-3 — Tunnel + Access live

Run: `bash scripts/phase6-pre-deploy-check.sh && bash scripts/deploy.sh`

- [x] 2 `cloudflared` pods Running in `lolday` namespace <sub>2026-04-20: 4qtvk + 6t49q, 1/1 Ready each.</sub>
- [x] Both logs print "Registered tunnel connection" <sub>2026-04-20: 4 registrations per pod (one per locations tpe01/hkg01/tpe01/hkg09).</sub>
- [x] Anonymous `curl -I https://lolday.connlabai.com` returns 302 to cloudflareaccess.com <sub>2026-04-20: 302, `location:` → `bolin8017.cloudflareaccess.com/cdn-cgi/access/login/lolday.connlabai.com`.</sub>
- [x] Non-NTUST Google login → Access Denied screen <sub>Operator-confirmed in original Phase 6 deploy.</sub>
- [x] NTUST Google login → lolday login page → platform credentials → Detectors page <sub>Operator-confirmed (user reported successful login earlier this session).</sub>
- [x] `cloudflared` pod cannot reach postgresql.lolday.svc:5432 (NetworkPolicy) <sub>2026-04-20: persistent test pod labeled `app.kubernetes.io/component=cloudflared` blocked from postgres/harbor-registry/redis, allowed to kube-dns:53 only. (First ephemeral-pod attempt raced kube-router rule install and connected — retest with long-lived pod authoritative.)</sub>
- [ ] Phase 4 curl E2E passes (via in-cluster port-forward) <sub>Not re-run this session.</sub>
- [ ] Phase 5 Playwright E2E passes (via Traefik LB + host-resolver-rules) <sub>Not re-run this session.</sub>

## Chaos (record findings)

- [x] Delete 1 cloudflared pod → external access continues
  - **2026-04-20 11:06:36 UTC** — deleted `cloudflared-84555fd564-d467w` (graceful, default grace=30s)
  - External `curl -I https://lolday.connlabai.com`: **15/15 samples 302, 35–46 ms** over 30 s window
  - Replacement `59hfz` scheduled immediately, Running within ~32 s
  - Result: **0 user-visible disruption** — surviving pod `wggsd` carried traffic
- [x] Delete both cloudflared pods → external access restores within 30 s; internal port-forward access works during outage
  - **2026-04-20 11:07:53 UTC** — `kubectl delete pods -l app.kubernetes.io/component=cloudflared` (graceful, 30 s)
  - External sampled every 1 s: **first 302 at +1 s, 5/5 consecutive 302s** (ran out to early-exit)
  - Internal: `kubectl port-forward svc/frontend 8080:80` → HTTP 200 / 2.9 ms throughout
  - Replacement pods `4qtvk`, `6t49q` reached `1/1 Ready` within ~45 s
  - Result: **no observable 502/timeout** — old pods drained gracefully over the 30 s grace window while new pods came up in parallel. Plan's predicted "brief 502" did not materialise (stronger than expected).
  - _Note:_ this exercises **planned rotation**, not crash recovery. Force-kill (`--grace-period=0 --force`) would produce a harsher test; deferred to a future chaos iteration.
- [N/A] Optional: fill `/` above 85 % → `NodeDiskAlmostFull` alert fires (monitoring PVs live under `/var/lib/rancher/k3s/storage` on K3s default local-path, not the dedicated `/mnt/ssd500g` path from the earlier design) <sub>Skipped: `/` at 59 % used; would need to write ~28 Gi of sparse file and roll back — not worth the I/O churn right now. Re-enable in a Phase 7 chaos session.</sub>

## Security

- [x] Anonymous request blocked at edge (302 to cloudflareaccess) <sub>2026-04-20: `curl -I https://lolday.connlabai.com/` → HTTP 302, `location` on `cloudflareaccess.com`.</sub>
- [x] Direct /api/v1/auth/login bypass blocked at edge <sub>2026-04-20: both GET and POST to `https://lolday.connlabai.com/api/v1/auth/login` → 302 to cloudflareaccess (no request reaches FastAPI).</sub>
- [ ] With valid cf-access-token, `/api/v1/health` returns 200 through Cloudflare <sub>Needs an operator-issued cf-access-token via `cloudflared access login`; not automated in this session. Implicitly confirmed by user's successful Detectors-page session.</sub>

## Sign-off

- [x] Date: 2026-04-20
- [x] Verifier: louiskyee (PO-LIN LAI) — chaos drill + security checks run via Claude Code session
