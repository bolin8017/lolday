#!/usr/bin/env bash
# Pre / post deploy verification for the lolday-jobs namespace migration.
#
# Spec: docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §9
#
# Usage:
#   bash scripts/migrate-jobs-namespace.sh check        # pre-deploy: ensure no in-flight vcjob
#   bash scripts/migrate-jobs-namespace.sh post-verify  # post-deploy: confirm migration landed
set -euo pipefail

OLD_NS=${OLD_NS:-lolday}
NEW_NS=${NEW_NS:-lolday-jobs}

mode=${1:-check}

case "$mode" in
  check)
    echo "[step 1/3] in-flight vcjobs in ${OLD_NS}"
    # Use jsonpath rather than column slicing — kubectl table output column
    # offsets vary across plugin versions and the awk approach mistakes
    # MINAVAILABLE for STATUS. Volcano Job state lives in .status.state.phase.
    in_flight_list=$(kubectl get jobs.batch.volcano.sh -n "${OLD_NS}" \
      -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.state.phase}{"\n"}{end}' \
      2>/dev/null \
      | awk -F'\t' '$2 != "Completed" && $2 != "Failed" && $2 != "Aborted" && $2 != ""' \
      || true)
    in_flight=$(printf '%s\n' "${in_flight_list}" | grep -cv '^$' || true)
    if [ "${in_flight}" -gt 0 ]; then
      echo "[fail] ${in_flight} in-flight vcjob(s) in ${OLD_NS}:"
      printf '  %s\n' "${in_flight_list}"
      echo "[hint] wait for them to finish (or cancel via UI) before cutover"
      exit 1
    fi
    echo "[ok] no in-flight vcjobs"

    echo ""
    echo "[step 2/3] in-flight build jobs in ${OLD_NS}"
    # batch.Job is "in-flight" when .status.succeeded != .spec.completions.
    in_flight_builds=$(kubectl get jobs.batch -n "${OLD_NS}" -l app=lolday-build \
      -o jsonpath='{range .items[?(@.status.succeeded < @.spec.completions)]}{.metadata.name}{"\n"}{end}' \
      2>/dev/null \
      | grep -c . || true)
    if [ "${in_flight_builds}" -gt 0 ]; then
      echo "[warn] ${in_flight_builds} in-flight build job(s) in ${OLD_NS} — they will continue in ${OLD_NS}"
      echo "[warn]   only newly-submitted builds will go to ${NEW_NS}"
    else
      echo "[ok] no in-flight builds"
    fi

    echo ""
    echo "[step 3/3] new ns existence (helm pre-create)"
    if kubectl get ns "${NEW_NS}" >/dev/null 2>&1; then
      echo "[ok] ${NEW_NS} already exists"
    else
      echo "[info] ${NEW_NS} not yet created — helm will create on next deploy"
    fi
    echo ""
    echo "=== READY FOR DEPLOY ==="
    ;;
  post-verify)
    echo "[step 1/4] new ns has Namespace + ResourceQuota + LimitRange"
    kubectl get ns,resourcequota,limitrange -n "${NEW_NS}"
    echo ""

    echo "[step 2/4] backend env updated"
    ns=$(kubectl -n "${OLD_NS}" get deploy backend \
      -o jsonpath='{.spec.template.spec.containers[*].env[?(@.name=="JOB_NAMESPACE")].value}')
    echo "  JOB_NAMESPACE=${ns}"
    if [ "${ns}" = "${NEW_NS}" ]; then
      echo "  [ok]"
    else
      echo "  [fail] expected ${NEW_NS}"
      exit 1
    fi
    echo ""

    echo "[step 3/4] backend RBAC reaches new ns"
    for verb in list create delete; do
      out=$(kubectl auth can-i "${verb}" jobs.batch.volcano.sh -n "${NEW_NS}" \
        --as="system:serviceaccount:${OLD_NS}:backend" 2>&1)
      echo "  can-i ${verb} vcjob in ${NEW_NS}: ${out}"
      [ "${out}" = "yes" ] || { echo "  [fail]"; exit 1; }
    done
    echo ""

    echo "[step 4/4] sample submit (operator runs from UI, this script just polls)"
    echo "  expect new vcjob to land in ${NEW_NS}, NOT ${OLD_NS}"
    echo ""
    echo "=== POST-VERIFY OK ==="
    ;;
  *)
    echo "usage: $0 [check|post-verify]" >&2
    exit 1
    ;;
esac
