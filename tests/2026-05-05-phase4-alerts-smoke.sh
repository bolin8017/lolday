#!/usr/bin/env bash
# Smoke: Phase 4 — resource-pressure alerts deployed.
#
# Spec: docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §7 Phase 4
set -euo pipefail

NS_MON=${NS_MON:-monitoring}
NS_INFRA=${NS_INFRA:-lolday}
fail=0

echo "[step 1/4] PrometheusRule lolday-baseline contains the new group"
groups=$(kubectl -n "${NS_MON}" get prometheusrule lolday-baseline \
  -o jsonpath='{.spec.groups[*].name}' 2>/dev/null)
if echo "${groups}" | grep -q "lolday-resource-pressure.rules"; then
  echo "OK: groups = ${groups}"
else
  echo "FAIL: lolday-resource-pressure.rules missing; groups=${groups}"
  fail=1
fi

echo ""
echo "[step 2/4] all 6 new alerts present"
expected="LoldayNodeMemoryPressure LoldayNodeDiskPressure LoldayGPUVRAMHigh LoldayJobsQuotaMemoryNearLimit LoldayJobsQuotaCPUNearLimit LoldayPendingJobsHigh"
alerts=$(kubectl -n "${NS_MON}" get prometheusrule lolday-baseline \
  -o jsonpath='{.spec.groups[*].rules[*].alert}' 2>/dev/null)
miss=0
for a in $expected; do
  if ! echo "${alerts}" | grep -q "${a}"; then
    echo "FAIL: missing ${a}"
    miss=$((miss+1))
  fi
done
if [ "${miss}" -eq 0 ]; then
  echo "OK: all 6 alerts present"
else
  echo "FAIL: ${miss} alert(s) missing"
  fail=1
fi

echo ""
echo "[step 3/4] backend exposes lolday_jobs_pending_total"
out=$(kubectl -n "${NS_INFRA}" exec deploy/backend -c backend -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/metrics').read().decode())" 2>/dev/null \
  | grep -E "^lolday_jobs_pending_total" \
  | head -1 || true)
if [ -n "${out}" ]; then
  echo "OK: ${out}"
else
  echo "FAIL: lolday_jobs_pending_total not in /metrics"
  fail=1
fi

echo ""
echo "[step 4/4] Prometheus is scraping the alert (rule is loaded)"
prom_pod=$(kubectl -n "${NS_MON}" get pods -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -n "${prom_pod}" ]; then
  loaded=$(kubectl -n "${NS_MON}" exec "${prom_pod}" -c prometheus -- \
    wget -q -O - http://localhost:9090/api/v1/rules 2>/dev/null \
    | grep -c "LoldayPendingJobsHigh" || true)
  if [ "${loaded}" -gt 0 ]; then
    echo "OK: Prometheus loaded LoldayPendingJobsHigh rule"
  else
    echo "WARN: Prometheus has not yet loaded the rule (may need 30-60s after deploy)"
  fi
else
  echo "WARN: could not find prometheus pod by label; skipping rules check"
fi

echo ""
if [ "${fail}" -eq 0 ]; then
  echo "=== SMOKE PASSED ==="
else
  echo "=== SMOKE FAILED ==="
  exit 1
fi
