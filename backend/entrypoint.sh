#!/bin/bash
set -e

# Run public API on :8000 and internal API on :8001.
# Each gets its own uvicorn process; SIGTERM is forwarded to both via trap.
# `wait -n` requires bash (not POSIX sh).
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 &
PID_PUBLIC=$!
uv run uvicorn app.internal_app:internal_app --host 0.0.0.0 --port 8001 &
PID_INTERNAL=$!

trap 'kill -TERM $PID_PUBLIC $PID_INTERNAL 2>/dev/null; wait $PID_PUBLIC $PID_INTERNAL' INT TERM

# If either dies, exit so K8s restarts the pod.
wait -n
EXIT_CODE=$?
kill -TERM $PID_PUBLIC $PID_INTERNAL 2>/dev/null || true
wait
exit $EXIT_CODE
