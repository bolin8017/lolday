# Phase 6 E2E Checklist

> Parallel to `phase4-e2e-checklist.md`. Run top-to-bottom after each sub-phase deploy.

## Prerequisites

- [ ] K3s `local-path` StorageClass exists, `/` has ≥ 60 Gi free (monitoring PVs use K3s default local-path)
- [ ] `~/.lolday-secrets.env` sources cleanly, contains `GRAFANA_ADMIN_PASSWORD`, `PG_EXPORTER_PASSWORD`, `CF_ENABLED`, `CF_TUNNEL_TOKEN`
- [ ] `connlabai.com` is in Cloudflare DNS
- [ ] Cloudflare Tunnel `lolday-server30` exists
- [ ] Access Application `lolday` with the NTUST policy exists

## Sub-phase 6-1 — Monitoring stack

Run: `bash scripts/phase6-pre-deploy-check.sh && bash scripts/deploy.sh`

- [ ] Prometheus / Alertmanager / kps-operator pods Running in `monitoring` ns
- [ ] Grafana / Loki / Promtail / kube-state-metrics / prometheus-node-exporter pods Running in `lolday` ns (Grafana subchart ignores parent namespaceOverride, so these land in release ns)
- [ ] Grafana reachable via `kubectl -n lolday port-forward svc/kps-grafana 3000:80`
- [ ] Grafana login works with `admin` / `$GRAFANA_ADMIN_PASSWORD`
- [ ] Dashboard "Kubernetes / Compute Resources / Cluster" has data
- [ ] Dashboard "NVIDIA DCGM Exporter Dashboard" shows both GPUs
- [ ] Dashboard "Traefik 3" shows request counters
- [ ] Dashboard "PostgreSQL Database" shows connections
- [ ] LogQL `{namespace="lolday"}` returns results (port-forward `svc/loki` in `lolday` ns on :3100, then hit `/loki/api/v1/query_range`)
- [ ] Prometheus `/api/v1/targets` shows ≥ 10 up jobs (port-forward `svc/kps-prometheus` in `monitoring` ns on :9090, then `curl http://localhost:9090/api/v1/targets`)
- [ ] Alertmanager UI (port-forward `svc/kps-alertmanager` in `monitoring` ns on :9093) lists 4 inactive baseline alerts
- [ ] Phase 4 curl E2E passes
- [ ] Phase 5 Playwright E2E passes

## Sub-phase 6-2 — Access policy

- [ ] Cloudflare → Zero Trust → Applications shows `lolday` with NTUST policy
- [ ] `cloudflared access login https://lolday.connlabai.com` issues a token for an NTUST Google account
- [ ] Same command with a non-NTUST account shows Access Denied

## Sub-phase 6-3 — Tunnel + Access live

Run: `bash scripts/phase6-pre-deploy-check.sh && bash scripts/deploy.sh`

- [ ] 2 `cloudflared` pods Running in `lolday` namespace
- [ ] Both logs print "Registered tunnel connection"
- [ ] Anonymous `curl -I https://lolday.connlabai.com` returns 302 to cloudflareaccess.com
- [ ] Non-NTUST Google login → Access Denied screen
- [ ] NTUST Google login → lolday login page → platform credentials → Detectors page
- [ ] `cloudflared` pod cannot reach postgresql.lolday.svc:5432 (NetworkPolicy)
- [ ] Phase 4 curl E2E passes (via in-cluster port-forward)
- [ ] Phase 5 Playwright E2E passes (via Traefik LB + host-resolver-rules)

## Chaos (record findings)

- [ ] Delete 1 cloudflared pod → external access continues
- [ ] Delete both cloudflared pods → external access restores within 30 s; internal port-forward access works during outage
- [ ] Optional: fill `/mnt/ssd500g/lolday-monitoring` → `NodeDiskAlmostFull` alert fires

## Security

- [ ] Anonymous request blocked at edge (302 to cloudflareaccess)
- [ ] Direct /api/v1/auth/login bypass blocked at edge
- [ ] With valid cf-access-token, `/api/v1/health` returns 200 through Cloudflare

## Sign-off

- [ ] Date: _____
- [ ] Verifier: _____
