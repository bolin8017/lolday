#!/usr/bin/env bats
# Phase 4 D4.5 R6 — smoke for scripts/check-user-facing-np.sh.
#
# The check-user-facing-np.sh shell wrapper is responsible for:
#   1. resolving CHART_DIR ($1 or default to ../charts/lolday)
#   2. erroring out if CHART_DIR doesn't exist
#   3. running `helm template ... --show-only ... --set <placeholders>`
#      against a tmp file with `set -euo pipefail` semantics
#   4. cd-ing to REPO_ROOT and dispatching to `python3 -m scripts.lib.np_check`
#
# The Python check logic itself is covered by scripts/tests/lib/test_np_check.py;
# these cases exercise the shell-level paths the unit tests cannot reach
# (PATH-resolved helm binary, mktemp + trap, CHART_DIR validation).
#
# Strategy: drop a stub `helm` script onto PATH that emits canned YAML for
# the case under test. The bats CI runner does not install helm, so this
# is the only viable way to exercise the wrapper end-to-end.

setup() {
  REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
  SCRIPT="${REPO_ROOT}/scripts/check-user-facing-np.sh"
  STUB_DIR="$(mktemp -d)"
  export PATH="${STUB_DIR}:${PATH}"
}

teardown() {
  rm -rf "$STUB_DIR"
}

# Emit a single NetworkPolicy YAML doc. Args:
#   $1 name, $2 podSelector key, $3 podSelector value, $4 POD-side port,
#   $5 ingress-from-ns label, $6 ingress-from-pod label
_render_np() {
  local name="$1" sel_k="$2" sel_v="$3" port="$4" from_ns="$5" from_pod="$6"
  cat <<YAML
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: ${name}
  namespace: lolday
spec:
  podSelector:
    matchLabels:
      ${sel_k}: ${sel_v}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ${from_ns}
          podSelector:
            matchLabels:
              app.kubernetes.io/name: ${from_pod}
      ports:
        - protocol: TCP
          port: ${port}
YAML
}

# Plant a helm stub on PATH that emits the literal $1 string when invoked.
_plant_helm_stub() {
  local body="$1"
  # The wrapper invokes `helm template ... >"$RENDERED" 2>/dev/null`; ignore
  # args and just emit the canned YAML.
  cat >"${STUB_DIR}/helm" <<EOF
#!/usr/bin/env bash
cat <<'STUB_YAML'
${body}
STUB_YAML
EOF
  chmod +x "${STUB_DIR}/helm"
}

@test "exits 1 with helpful error when CHART_DIR does not exist" {
  run bash "${SCRIPT}" /this/path/definitely/does/not/exist
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "chart dir not found"
}

@test "exits 0 with OK message when helm output satisfies all 3 targets" {
  YAML="$(_render_np backend-metrics-from-monitoring-only app.kubernetes.io/component backend 8000 kube-system traefik)"
  YAML="${YAML}
$(_render_np frontend-ingress-allow app frontend 8080 kube-system traefik)"
  YAML="${YAML}
$(_render_np mlflow-ingress-allow app.kubernetes.io/component mlflow 5000 kube-system traefik)"
  _plant_helm_stub "$YAML"
  run bash "${SCRIPT}" "${REPO_ROOT}/charts/lolday"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "OK: 3 user-facing pod(s)"
}

@test "exits 1 when helm output is missing a target NetworkPolicy" {
  # Render only backend + frontend; mlflow-ingress-allow is absent.
  YAML="$(_render_np backend-metrics-from-monitoring-only app.kubernetes.io/component backend 8000 kube-system traefik)"
  YAML="${YAML}
$(_render_np frontend-ingress-allow app frontend 8080 kube-system traefik)"
  _plant_helm_stub "$YAML"
  run bash "${SCRIPT}" "${REPO_ROOT}/charts/lolday"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "mlflow-ingress-allow"
  echo "$output" | grep -q "not rendered"
}

@test "exits 1 when ingress source is cloudflared instead of traefik (P1 H-25 regression class)" {
  # The literal bug shipped in commit 06715ef.
  YAML="$(_render_np backend-metrics-from-monitoring-only app.kubernetes.io/component backend 8000 lolday cloudflared)"
  YAML="${YAML}
$(_render_np frontend-ingress-allow app frontend 8080 kube-system traefik)"
  YAML="${YAML}
$(_render_np mlflow-ingress-allow app.kubernetes.io/component mlflow 5000 kube-system traefik)"
  _plant_helm_stub "$YAML"
  run bash "${SCRIPT}" "${REPO_ROOT}/charts/lolday"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "backend-metrics-from-monitoring-only"
  echo "$output" | grep -q "kube-system + app.kubernetes.io/name=traefik"
  # Cross-link the troubleshooting runbook entry that operators reach for.
  echo "$output" | grep -q "troubleshooting.md"
}
