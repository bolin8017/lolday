#!/bin/bash
set -e

# Run public API on :8000 and internal API on :8001.
# Each gets its own uvicorn process; SIGTERM is forwarded to both via trap.
# `wait -n` requires bash (not POSIX sh).
#
# #164: --proxy-headers --forwarded-allow-ips='*' makes uvicorn trust the
# X-Forwarded-* headers that Traefik attaches. Without these flags
# request.client.host resolves to the in-cluster pod IP (always the
# Traefik pod), which breaks rate_limit_ip (M-rate-limit-ip / H-26) and
# strips access-log forensic value. K3s' default Traefik strips inbound
# XFF and re-adds its own headers, so trusting '*' is safe behind the
# in-cluster Service mesh. Internal sub-app (:8001) is only reached by
# job pods inside the cluster -- trust the same way for symmetry.
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips='*' &
PID_PUBLIC=$!
uv run uvicorn app.internal_app:internal_app --host 0.0.0.0 --port 8001 \
  --proxy-headers --forwarded-allow-ips='*' &
PID_INTERNAL=$!

trap 'kill -TERM $PID_PUBLIC $PID_INTERNAL 2>/dev/null; wait $PID_PUBLIC $PID_INTERNAL' INT TERM

# If either dies, exit so K8s restarts the pod.
wait -n
EXIT_CODE=$?
kill -TERM $PID_PUBLIC $PID_INTERNAL 2>/dev/null || true
wait
exit $EXIT_CODE
