# Phase 6 E2E Checklist

> Parallel to `phase4-e2e-checklist.md`. Run top-to-bottom after each sub-phase deploy.

## Prerequisites

- [ ] `/mnt/ssd500g/lolday-monitoring` exists, writable, ≥ 60 Gi free
- [ ] `~/.lolday-secrets.env` sources cleanly, contains `GRAFANA_ADMIN_PASSWORD`, `PG_EXPORTER_PASSWORD`, `CF_ENABLED`, `CF_TUNNEL_TOKEN`
- [ ] `connlabai.com` is in Cloudflare DNS
- [ ] Cloudflare Tunnel `lolday-server30` exists
- [ ] Access Application `lolday` with the NTUST policy exists

## Sub-phase 6-1 — Monitoring stack

Run: `bash scripts/phase6-pre-deploy-check.sh && bash scripts/deploy.sh`

- [ ] All pods in `monitoring` namespace Running
- [ ] Grafana reachable via `kubectl -n monitoring port-forward svc/kps-kube-prometheus-stack-grafana 3000:80`
- [ ] Grafana login works with `admin` / `$GRAFANA_ADMIN_PASSWORD`
- [ ] Dashboard "Kubernetes / Compute Resources / Cluster" has data
- [ ] Dashboard "NVIDIA DCGM Exporter Dashboard" shows both GPUs
- [ ] Dashboard "Traefik 3" shows request counters
- [ ] Dashboard "PostgreSQL Database" shows connections
- [ ] LogQL `{namespace="lolday"}` returns results
- [ ] `curl /api/v1/targets` on Prometheus shows ≥ 10 up jobs
- [ ] Alertmanager UI (port-forward 9093) lists 4 inactive baseline alerts
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
