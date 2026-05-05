# Phase 1: `lolday-jobs` Namespace + ResourceQuota + LimitRange Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate detector vcjobs + BuildKit jobs into a dedicated `lolday-jobs` namespace, then cap that namespace with `ResourceQuota` + `LimitRange`. Add an `lolday-infra-quota` to the existing `lolday` namespace so prometheus / mlflow / harbor can't run away either. Closes spec §6.2 (why namespace separation is necessary) + §7 Phase 1.

**Architecture:** Cross-namespace migration. Backend pod stays in `lolday`; vcjobs / build Jobs / their secrets / their NetworkPolicies move to `lolday-jobs`. Backend SA gets a second Role/RoleBinding scoped to `lolday-jobs` (Phase 7.5 deliberately narrowed pods Role to JOB_NAMESPACE — preserving that pattern, not widening). NetworkPolicy `namespaceSelector`s switch from same-ns to cross-ns. Idempotent Helm rollout; old finished vcjobs in `lolday` decay via `ttlSecondsAfterFinished=7d`.

**Tech Stack:** Helm 3, Kubernetes API (Namespace, ResourceQuota, LimitRange, Role, RoleBinding, NetworkPolicy), Volcano `batch.volcano.sh/v1alpha1.Job`.

**Spec:** `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` — read §4.2 (current state), §6.2 (why separate ns), §7 Phase 1 (concrete numbers), §9 (migration path), §11 (rollback) before starting.

**Pre-requisite:** Phase 0 (PR #86 / `feat(infra): phase 0 — host-level kubelet reservations`) **already merged + applied on server30**. Allocatable.memory ≈ 55 GiB. Without Phase 0 the new `requests.memory: 30Gi` cap might leave too little headroom.

---

## Reference: chosen namespace + quota values

| Resource                                              | Value         | Source / why                                                                |
| ----------------------------------------------------- | ------------- | --------------------------------------------------------------------------- |
| New namespace name                                    | `lolday-jobs` | spec §6.2                                                                   |
| `lolday-jobs` `ResourceQuota.requests.cpu`            | 8             | 80% of post-Phase-0 allocatable 10                                          |
| `lolday-jobs` `ResourceQuota.requests.memory`         | 30Gi          | spec §7 Phase 1 — leaves 25Gi for `lolday` infra (allocatable 55 − 30 = 25) |
| `lolday-jobs` `ResourceQuota.limits.cpu`              | 24            | overcommit 2.4× over requests; CFS handles                                  |
| `lolday-jobs` `ResourceQuota.limits.memory`           | 50Gi          | overcommit; cgroup OOM-kills offending pod, not host                        |
| `lolday-jobs` `ResourceQuota.requests.nvidia.com/gpu` | 2             | total cluster GPU                                                           |
| `lolday-jobs` `ResourceQuota.count/pods`              | 16            | 2 GPU2 jobs × 5 containers each = 10, + 6 build pod headroom                |
| `lolday-jobs` `LimitRange.max.memory`                 | 16Gi          | matches current detector container limits.memory                            |
| `lolday-jobs` `LimitRange.max.cpu`                    | 4             | matches current detector container limits.cpu                               |
| `lolday-jobs` `LimitRange.default.memory`             | 4Gi           | matches current detector requests.memory                                    |
| `lolday-jobs` `LimitRange.default.cpu`                | 2             | matches current detector requests.cpu                                       |
| `lolday-jobs` `LimitRange.defaultRequest.memory`      | 1Gi           | sidecar + init class                                                        |
| `lolday-jobs` `LimitRange.defaultRequest.cpu`         | 500m          | sidecar + init class                                                        |
| `lolday` `ResourceQuota.requests.memory`              | 20Gi          | infra namespace quota — caps prometheus/mlflow/postgres aggregate           |
| `lolday` `ResourceQuota.limits.memory`                | 40Gi          | overcommit                                                                  |

> **Decision recap (from spec §6.2)**: do NOT set CPU / GPU quota on `lolday` infra ns — infra is monitoring + persistence, not GPU-aware. Memory cap alone is enough.

---

## File map

**New files:**

- `charts/lolday/templates/jobs-namespace.yaml`
- `charts/lolday/templates/jobs-quota.yaml`
- `charts/lolday/templates/jobs-limitrange.yaml`
- `charts/lolday/templates/lolday-quota.yaml`
- `charts/lolday/templates/jobs-rbac.yaml` — Role + RoleBinding in `lolday-jobs` for backend SA
- `scripts/migrate-jobs-namespace.sh` — pre-deploy verification + cleanup helper
- `tests/2026-05-05-jobs-namespace-smoke.sh`

**Modified files:**

- `charts/lolday/values.yaml` — add `global.jobsNamespace: lolday-jobs`
- `charts/lolday/templates/backend.yaml` — `BUILD_NAMESPACE` + `JOB_NAMESPACE` env switch from `global.namespace` to `global.jobsNamespace`
- `charts/lolday/templates/job-networkpolicy.yaml` — re-scope to `global.jobsNamespace` + cross-ns `namespaceSelector` for backend / mlflow targets
- `charts/lolday/templates/build-networkpolicy.yaml` — re-scope + cross-ns
- `charts/lolday/templates/backend-rbac.yaml` — slim the same-ns Role (drop pods/secrets/configmaps/jobs verbs that now belong to `jobs-rbac.yaml`); keep PVC + build (legacy) verbs in same ns. **Re-think during Step**: keep both Roles or merge — see Task 8 decision point.
- `docs/architecture.md` — namespace section refresh; mark §9 namespace separation as resolved
- `docs/runbooks/deploy.md` §9 — append migration runbook
- `.claude/rules/charts-and-helm.md` — note the new `lolday-jobs` template family

**Not touched:** anything under `backend/`, `frontend/`, `tests/phase7/` (will not regress; Phase 1 is chart-only). Phase 7.5 backend code paths assume `JOB_NAMESPACE` env — already configurable, no code change needed.

---

## Execution order

```
Wave 0 (parallel where independent — chart authoring)
├── Task 1: branch confirmation
├── Task 2: values.yaml — add global.jobsNamespace
├── Task 3: jobs-namespace.yaml
├── Task 4: jobs-quota.yaml
├── Task 5: jobs-limitrange.yaml
├── Task 6: lolday-quota.yaml
└── Task 7: jobs-rbac.yaml

Wave 1 (sequential — cross-touching files)
├── Task 8: backend-rbac.yaml slimming + cross-check with Task 7
├── Task 9: backend.yaml env switch
├── Task 10: job-networkpolicy.yaml re-scope
├── Task 11: build-networkpolicy.yaml re-scope
└── Task 12: helm lint + helm template diff review

Wave 2 (sequential — verification artefacts)
├── Task 13: scripts/migrate-jobs-namespace.sh
├── Task 14: tests/2026-05-05-jobs-namespace-smoke.sh
└── Task 15: docs updates (architecture, runbook, rules)

Wave 3 (sequential — close out)
├── Task 16: pre-commit + commit
├── Task 17: push + PR
├── Task 18: deploy.sh on server30 (operator-attended; idempotent)
└── Task 19: smoke + verify; rollback if needed
```

Wave 0–2 = pure Claude. Wave 3 Task 18 = operator-attended `bash scripts/deploy.sh`. Task 19 = post-apply Claude verifies.

---

## Task 1: Branch confirmation

- [ ] **Step 1: Verify branch**

```bash
git rev-parse --abbrev-ref HEAD
```

Expected: `feat/gpu-scheduling-phase1-jobs-namespace` (already created earlier in session).

If different:

```bash
git checkout main
git pull --rebase
git checkout -b feat/gpu-scheduling-phase1-jobs-namespace
```

- [ ] **Step 2: Confirm Phase 0 merged on main**

```bash
git log --oneline main | grep "phase 0 — host-level kubelet" | head -1
```

Expected: 1 line referencing the merge commit (currently `eb2dfa5`).

---

## Task 2: `values.yaml` — add `global.jobsNamespace`

**Files:**

- Modify: `charts/lolday/values.yaml:5-7`

- [ ] **Step 1: Edit `global` block**

Find:

```yaml
global:
  namespace: lolday
```

Replace with:

```yaml
global:
  namespace: lolday
  # Phase 1 (2026-05-05) — detector vcjobs + BuildKit Jobs run in this
  # dedicated namespace so a per-namespace ResourceQuota / LimitRange
  # can cap them without constraining infra (postgres / prometheus / etc.).
  # See docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §6.2
  jobsNamespace: lolday-jobs
```

- [ ] **Step 2: Sanity grep**

```bash
grep -A 8 "^global:" charts/lolday/values.yaml | head -10
```

Expected: shows both `namespace: lolday` and `jobsNamespace: lolday-jobs`.

---

## Task 3: `templates/jobs-namespace.yaml`

**Files:**

- Create: `charts/lolday/templates/jobs-namespace.yaml`

- [ ] **Step 1: Author**

```yaml
{{/* Phase 1 — dedicated namespace for detector vcjobs + BuildKit Jobs.
     Decoupled from `global.namespace` so a per-namespace
     ResourceQuota / LimitRange can cap workload pods without
     constraining infra. See spec §6.2. */}}
apiVersion: v1
kind: Namespace
metadata:
  name: {{ .Values.global.jobsNamespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
    lolday.io/role: workload
    # explicit duplicate of K8s' auto-injected label, for older K3s + tooling
    # that don't reliably honour the projected `kubernetes.io/metadata.name`.
    kubernetes.io/metadata.name: {{ .Values.global.jobsNamespace }}
```

- [ ] **Step 2: Verify rendering**

```bash
helm template charts/lolday --show-only templates/jobs-namespace.yaml | head -10
```

Expected: a `Namespace` object with `name: lolday-jobs`.

---

## Task 4: `templates/jobs-quota.yaml`

**Files:**

- Create: `charts/lolday/templates/jobs-quota.yaml`

- [ ] **Step 1: Author**

```yaml
{{/* Phase 1 — total resource cap on lolday-jobs namespace.
     Numbers from spec §7 Phase 1. */}}
apiVersion: v1
kind: ResourceQuota
metadata:
  name: lolday-jobs-quota
  namespace: {{ .Values.global.jobsNamespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  hard:
    requests.cpu: "8"
    requests.memory: 30Gi
    limits.cpu: "24"
    limits.memory: 50Gi
    requests.nvidia.com/gpu: "2"
    count/pods: "16"
    count/jobs.batch: "10"
    count/jobs.batch.volcano.sh: "20"
```

- [ ] **Step 2: Verify rendering**

```bash
helm template charts/lolday --show-only templates/jobs-quota.yaml | head -20
```

Expected: ResourceQuota in `lolday-jobs` ns with the above values.

---

## Task 5: `templates/jobs-limitrange.yaml`

**Files:**

- Create: `charts/lolday/templates/jobs-limitrange.yaml`

- [ ] **Step 1: Author**

```yaml
{{/* Phase 1 — per-container default + max for lolday-jobs.
     `default` / `defaultRequest` apply when a manifest omits
     limits/requests; `max` is the per-container hard ceiling.
     Numbers from spec §7 Phase 1. */}}
apiVersion: v1
kind: LimitRange
metadata:
  name: lolday-jobs-limits
  namespace: {{ .Values.global.jobsNamespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  limits:
    - type: Container
      max:
        cpu: "4"
        memory: 16Gi
      default:
        cpu: "2"
        memory: 4Gi
      defaultRequest:
        cpu: 500m
        memory: 1Gi
```

- [ ] **Step 2: Verify rendering**

```bash
helm template charts/lolday --show-only templates/jobs-limitrange.yaml | head -25
```

---

## Task 6: `templates/lolday-quota.yaml`

**Files:**

- Create: `charts/lolday/templates/lolday-quota.yaml`

- [ ] **Step 1: Author**

```yaml
{{/* Phase 1 — memory-only cap on the lolday infra namespace
     (postgres / mlflow / harbor / kps / etc.). CPU + GPU intentionally
     not capped because infra is not GPU-aware and CPU contention is
     handled by CFS. See spec §6.2. */}}
apiVersion: v1
kind: ResourceQuota
metadata:
  name: lolday-infra-quota
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  hard:
    requests.memory: 20Gi
    limits.memory: 40Gi
```

- [ ] **Step 2: Render-time check that current usage fits**

```bash
kubectl get pods -n lolday \
  -o go-template='{{range .items}}{{range .spec.containers}}{{.resources.requests.memory}}{{"\n"}}{{end}}{{end}}' \
  | python3 -c '
import sys
total = 0
for line in sys.stdin:
    s = line.strip()
    if not s: continue
    if s.endswith("Mi"): total += int(s[:-2]) / 1024
    elif s.endswith("Gi"): total += float(s[:-2])
print(f"current requests.memory total: {total:.2f} GiB")
print(f"new quota: 20 GiB")
'
```

Expected: current total ≪ 20 GiB. If close, **stop and revisit** the cap.

---

## Task 7: `templates/jobs-rbac.yaml`

**Files:**

- Create: `charts/lolday/templates/jobs-rbac.yaml`

> **Why a separate file**: backend SA stays in `lolday`, but it needs to manage vcjobs / build jobs / their secrets in the new ns. K8s RoleBindings can reference a SA from another namespace, so we don't need to create a duplicate SA in `lolday-jobs`.

- [ ] **Step 1: Author**

```yaml
{{- if .Values.backend.enabled }}
{{/* Phase 1 — Role + RoleBinding in lolday-jobs that lets the backend
     ServiceAccount in lolday manage vcjobs / build Jobs / their secrets.
     Phase 7.5 deliberately narrowed Roles to the workload namespace; we
     preserve that pattern by adding a second Role here, NOT widening the
     existing same-ns Role into a ClusterRole. */}}
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: backend-jobs
  namespace: {{ .Values.global.jobsNamespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
rules:
  - apiGroups: [""]
    resources: [pods, pods/log]
    verbs: [get, list, watch]
  - apiGroups: [""]
    resources: [secrets, configmaps]
    verbs: [get, list, create, update, delete]
  - apiGroups: [batch]
    resources: [jobs]
    verbs: [get, list, create, delete, watch]
  - apiGroups: [batch.volcano.sh]
    resources: [jobs]
    verbs: [get, list, create, delete, watch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: backend-jobs
  namespace: {{ .Values.global.jobsNamespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
subjects:
  - kind: ServiceAccount
    name: backend
    namespace: {{ .Values.global.namespace }}
roleRef:
  kind: Role
  name: backend-jobs
  apiGroup: rbac.authorization.k8s.io
{{- end }}
```

---

## Task 8: `templates/backend-rbac.yaml` — slim the same-ns Role

**Files:**

- Modify: `charts/lolday/templates/backend-rbac.yaml`

> **Decision point**: do we strip `pods`/`secrets`/`configmaps`/`batch.jobs`/`batch.volcano.sh.jobs` from the same-ns Role since they're now duplicated in `jobs-rbac.yaml`?
>
> **Choice: keep `secrets` + `configmaps` in `lolday` Role** — backend may still create same-ns helper resources (e.g. `backend.lolday.svc` Secret for itself, MLflow auth bits). Strip the rest.
>
> **Strip from same-ns Role:** `pods`, `pods/log`, `batch.jobs`, `batch.volcano.sh.jobs`. **Keep:** `secrets`, `configmaps`, `persistentvolumeclaims`.

- [ ] **Step 1: Edit the Role rules block**

Find:

```yaml
rules:
  - apiGroups: [""]
    resources: [pods, pods/log]
    verbs: [get, list, watch]
  - apiGroups: [""]
    resources: [secrets, configmaps]
    verbs: [get, list, create, update, delete]
  - apiGroups: [""]
    resources: [persistentvolumeclaims]
    verbs: [get, list, watch]
  - apiGroups: [batch]
    resources: [jobs]
    verbs: [get, list, create, delete, watch]
  # Phase 7.3 + 7.4 — Volcano training Jobs: reconciler reads + deletes,
  # cluster_status.get_queue_depth lists.
  - apiGroups: [batch.volcano.sh]
    resources: [jobs]
    verbs: [get, list, create, delete, watch]
```

Replace with:

```yaml
rules:
  # Phase 1 (2026-05-05) — pods / batch / batch.volcano.sh moved to
  # the lolday-jobs Role (templates/jobs-rbac.yaml). Same-ns Role keeps
  # only resources that legitimately live in the infra namespace.
  - apiGroups: [""]
    resources: [secrets, configmaps]
    verbs: [get, list, create, update, delete]
  - apiGroups: [""]
    resources: [persistentvolumeclaims]
    verbs: [get, list, watch]
```

- [ ] **Step 2: Verify ClusterRoleBinding name still uses `global.namespace`**

The bottom block (`{{ .Values.global.namespace }}-backend-cluster-reader`) keeps the old name — it's just a stable identifier, not a namespace assertion. Leave untouched.

---

## Task 9: `templates/backend.yaml` — switch JOB / BUILD namespace

**Files:**

- Modify: `charts/lolday/templates/backend.yaml:65-68`

- [ ] **Step 1: Edit the env block**

Find:

```yaml
# Phase 7.5: derive BUILD_NAMESPACE + JOB_NAMESPACE from the release
# namespace so they cannot drift out of sync with the namespaced
# pods Role (which only grants list/get in this ns). A mismatch
# silently 403s GPU accounting → `in_use=0` → over-schedule.
- name: BUILD_NAMESPACE
  value: { { .Values.global.namespace | quote } }
- name: JOB_NAMESPACE
  value: { { .Values.global.namespace | quote } }
```

Replace with:

```yaml
# Phase 1 (2026-05-05): vcjobs + build Jobs moved to the
# dedicated jobsNamespace. The Phase 7.5 invariant — these
# two stay in sync with the corresponding RBAC scope —
# holds because templates/jobs-rbac.yaml grants the
# backend SA verbs in jobsNamespace; templates/backend-rbac.yaml
# only retains same-ns secrets/configmaps/PVC verbs.
- name: BUILD_NAMESPACE
  value: { { .Values.global.jobsNamespace | quote } }
- name: JOB_NAMESPACE
  value: { { .Values.global.jobsNamespace | quote } }
```

---

## Task 10: `templates/job-networkpolicy.yaml` — re-scope + cross-ns

**Files:**

- Modify: `charts/lolday/templates/job-networkpolicy.yaml`

- [ ] **Step 1: Replace the whole file**

```yaml
{{- if .Values.jobs.networkPolicy.enabled }}
{{/* Phase 1 — NP scoped to lolday-jobs ns; egress targets resolve to lolday
     infra ns via cross-ns namespaceSelector. */}}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: lolday-job-egress
  namespace: {{ .Values.global.jobsNamespace }}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: lolday-job
  policyTypes: [Ingress, Egress]
  ingress: []
  egress:
    # DNS
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - { protocol: UDP, port: 53 }
        - { protocol: TCP, port: 53 }
    # MLflow (detector container) — cross-ns to lolday infra
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.global.namespace }}
          podSelector:
            matchLabels:
              app.kubernetes.io/component: mlflow
      ports:
        - { protocol: TCP, port: 5000 }
    # Backend (config-writer init container, event tailer sidecar) — cross-ns
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.global.namespace }}
          podSelector:
            matchLabels:
              app.kubernetes.io/component: backend
      ports:
        - { protocol: TCP, port: 8000 }
{{- end }}
```

---

## Task 11: `templates/build-networkpolicy.yaml` — re-scope + cross-ns

**Files:**

- Modify: `charts/lolday/templates/build-networkpolicy.yaml`

- [ ] **Step 1: Replace the whole file**

```yaml
{{- if .Values.backend.enabled }}
{{/* Phase 1 — NP scoped to lolday-jobs; harbor + backend live in lolday infra ns. */}}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: lolday-build-egress
  namespace: {{ .Values.global.jobsNamespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app: lolday-build
  policyTypes: [Egress]
  egress:
    # DNS
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
    # Harbor (subchart in lolday infra ns)
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.global.namespace }}
          podSelector:
            matchLabels:
              app: harbor
    # Backend (validate container schema callback)
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.global.namespace }}
          podSelector:
            matchLabels:
              app.kubernetes.io/component: backend
      ports:
        - protocol: TCP
          port: 8000
    # Internet, excluding cluster internal ranges
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.42.0.0/16
              - 10.43.0.0/16
              - 192.168.0.0/16
              - 172.16.0.0/12
              - 169.254.0.0/16
{{- end }}
```

---

## Task 12: helm lint + helm template diff review

- [ ] **Step 1: lint**

```bash
helm lint charts/lolday
```

Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 2: template render**

```bash
helm template charts/lolday > /tmp/phase1-rendered.yaml
echo "rendered $(wc -l < /tmp/phase1-rendered.yaml) lines"
grep -c "^kind:" /tmp/phase1-rendered.yaml
```

- [ ] **Step 3: spot-check the new objects exist**

```bash
grep -E "^(kind|  name|  namespace):" /tmp/phase1-rendered.yaml | grep -B1 -A1 "lolday-jobs"
```

Expected entries:

- `Namespace lolday-jobs`
- `ResourceQuota lolday-jobs-quota` in `lolday-jobs`
- `LimitRange lolday-jobs-limits` in `lolday-jobs`
- `ResourceQuota lolday-infra-quota` in `lolday`
- `Role backend-jobs` in `lolday-jobs`
- `RoleBinding backend-jobs` in `lolday-jobs`
- `NetworkPolicy lolday-job-egress` in `lolday-jobs`
- `NetworkPolicy lolday-build-egress` in `lolday-jobs`

---

## Task 13: `scripts/migrate-jobs-namespace.sh`

**Files:**

- Create: `scripts/migrate-jobs-namespace.sh`

> Pre-deploy verification: confirm the jobs ns can be cleanly cut over (no in-flight vcjob in old ns).

- [ ] **Step 1: Author**

```bash
#!/usr/bin/env bash
# Pre / post deploy verification for the lolday-jobs namespace migration.
#
# Spec: docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §9
set -euo pipefail

OLD_NS=${OLD_NS:-lolday}
NEW_NS=${NEW_NS:-lolday-jobs}

mode=${1:-check}

case "$mode" in
  check)
    echo "[step 1/3] in-flight vcjobs in ${OLD_NS}"
    kubectl get jobs.batch.volcano.sh -n "${OLD_NS}" 2>/dev/null \
      | awk 'NR>1 && $3 != "Completed" && $3 != "Failed" {print}' \
      || true
    in_flight=$(kubectl get jobs.batch.volcano.sh -n "${OLD_NS}" 2>/dev/null \
      | awk 'NR>1 && $3 != "Completed" && $3 != "Failed"' \
      | wc -l)
    if [ "${in_flight}" -gt 0 ]; then
      echo "[fail] ${in_flight} in-flight vcjob(s) in ${OLD_NS} — wait for them to finish before cutover"
      exit 1
    fi
    echo "[ok] no in-flight vcjobs"

    echo ""
    echo "[step 2/3] in-flight build jobs in ${OLD_NS}"
    in_flight_builds=$(kubectl get jobs.batch -n "${OLD_NS}" -l app=lolday-build 2>/dev/null \
      | awk 'NR>1 && $2 != "Complete"' \
      | wc -l)
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
    kubectl -n "${OLD_NS}" get deploy backend \
      -o jsonpath='{.spec.template.spec.containers[*].env[?(@.name=="JOB_NAMESPACE")].value}'
    echo ""

    echo ""
    echo "[step 3/4] sample submit (operator runs from UI, this script just polls)"
    echo "[info] expect new vcjob to land in ${NEW_NS}, NOT ${OLD_NS}"

    echo ""
    echo "[step 4/4] backend RBAC reaches new ns"
    kubectl -n "${NEW_NS}" auth can-i list pods --as=system:serviceaccount:"${OLD_NS}":backend
    kubectl -n "${NEW_NS}" auth can-i create jobs.batch.volcano.sh --as=system:serviceaccount:"${OLD_NS}":backend
    ;;
  *)
    echo "usage: $0 [check|post-verify]"
    exit 1
    ;;
esac
```

- [ ] **Step 2: Make executable + syntax check**

```bash
chmod +x scripts/migrate-jobs-namespace.sh
bash -n scripts/migrate-jobs-namespace.sh
```

---

## Task 14: `tests/2026-05-05-jobs-namespace-smoke.sh`

**Files:**

- Create: `tests/2026-05-05-jobs-namespace-smoke.sh`

- [ ] **Step 1: Author**

```bash
#!/usr/bin/env bash
# Smoke: Phase 1 — lolday-jobs namespace migration landed correctly.
set -euo pipefail

NS_INFRA=${NS_INFRA:-lolday}
NS_JOBS=${NS_JOBS:-lolday-jobs}
fail=0

echo "[step 1/6] new namespace exists"
kubectl get ns "${NS_JOBS}" >/dev/null 2>&1 \
  && echo "OK" \
  || { echo "FAIL: ${NS_JOBS} missing"; fail=1; }

echo ""
echo "[step 2/6] ResourceQuota in lolday-jobs"
kubectl -n "${NS_JOBS}" get resourcequota lolday-jobs-quota -o jsonpath='{.spec.hard}' 2>/dev/null \
  | python3 -c '
import sys, json
d = json.load(sys.stdin)
errs = []
if d.get("requests.memory") != "30Gi": errs.append(f"requests.memory={d.get(\"requests.memory\")}")
if d.get("limits.memory") != "50Gi": errs.append(f"limits.memory={d.get(\"limits.memory\")}")
if d.get("requests.nvidia.com/gpu") != "2": errs.append(f"requests.gpu={d.get(\"requests.nvidia.com/gpu\")}")
if errs: print("FAIL:", *errs); sys.exit(1)
print("OK")
' || fail=1

echo ""
echo "[step 3/6] LimitRange in lolday-jobs"
kubectl -n "${NS_JOBS}" get limitrange lolday-jobs-limits -o jsonpath='{.spec.limits[0].max}' 2>/dev/null \
  | python3 -c '
import sys, json
d = json.load(sys.stdin)
if d.get("memory") == "16Gi" and d.get("cpu") == "4":
    print("OK")
else:
    print(f"FAIL: max={d}"); sys.exit(1)
' || fail=1

echo ""
echo "[step 4/6] ResourceQuota in lolday infra"
kubectl -n "${NS_INFRA}" get resourcequota lolday-infra-quota >/dev/null 2>&1 \
  && echo "OK" \
  || { echo "FAIL: lolday-infra-quota missing"; fail=1; }

echo ""
echo "[step 5/6] backend SA can manage vcjobs in lolday-jobs"
out=$(kubectl auth can-i create jobs.batch.volcano.sh -n "${NS_JOBS}" \
  --as="system:serviceaccount:${NS_INFRA}:backend" 2>&1)
case "${out}" in
  yes) echo "OK" ;;
  *) echo "FAIL: backend SA cannot create vcjobs in ${NS_JOBS}: ${out}"; fail=1 ;;
esac

echo ""
echo "[step 6/6] backend env JOB_NAMESPACE points to lolday-jobs"
ns=$(kubectl -n "${NS_INFRA}" get deploy backend \
  -o jsonpath='{.spec.template.spec.containers[*].env[?(@.name=="JOB_NAMESPACE")].value}')
if [ "${ns}" = "${NS_JOBS}" ]; then
  echo "OK"
else
  echo "FAIL: JOB_NAMESPACE=${ns}, expected ${NS_JOBS}"
  fail=1
fi

echo ""
if [ "${fail}" -eq 0 ]; then
  echo "=== SMOKE PASSED ==="
else
  echo "=== SMOKE FAILED ==="
  exit 1
fi
```

- [ ] **Step 2: Make executable + syntax check**

```bash
chmod +x tests/2026-05-05-jobs-namespace-smoke.sh
bash -n tests/2026-05-05-jobs-namespace-smoke.sh
```

---

## Task 15: docs updates

**Files:**

- Modify: `docs/architecture.md` — namespace section + §9 tech debt entry
- Modify: `docs/runbooks/deploy.md` §9 Maintenance — append migration runbook
- Modify: `.claude/rules/charts-and-helm.md` — note new `lolday-jobs` template family

- [ ] **Step 1: `docs/architecture.md`** — find the architecture section that lists namespaces, append `lolday-jobs` entry

Around §3 component table, ensure no stale references to "everything in lolday ns". Add a paragraph (after §5.3 Harbor DNS, new §5.4):

```markdown
### 5.4 Two-namespace model (since 2026-05-05)

- `lolday` — infrastructure: backend, frontend, postgres, redis, mlflow, harbor, kps, loki, alloy, trivy, cloudflared. Memory cap `lolday-infra-quota: requests.memory 20Gi, limits.memory 40Gi`.
- `lolday-jobs` — workload: detector vcjobs (`batch.volcano.sh/v1alpha1.Job`) + BuildKit build Jobs. Capped by `lolday-jobs-quota` (`requests.memory 30Gi, limits.memory 50Gi, requests.nvidia.com/gpu 2, count/pods 16`) and `lolday-jobs-limits` LimitRange (per-container `max: 16Gi memory / 4 cpu`).
- Backend SA (`lolday/backend`) has two Roles: same-ns Role for secrets / configmaps / PVCs; cross-ns Role `backend-jobs` in `lolday-jobs` for pods / batch / batch.volcano.sh.
- NetworkPolicies on `lolday-job-egress` / `lolday-build-egress` use `namespaceSelector kubernetes.io/metadata.name: lolday` to target backend / mlflow / harbor across the namespace boundary.
```

- [ ] **Step 2: `docs/architecture.md` §9 tech debt** — add a resolved entry

After the existing item 14 (frontend schema drift), insert:

```markdown
15. ~~**Single-namespace deploy**~~ — resolved 2026-05-05 in `feat/gpu-scheduling-phase1-jobs-namespace`: detector vcjobs + BuildKit Jobs migrated to a dedicated `lolday-jobs` namespace so per-namespace `ResourceQuota` + `LimitRange` can cap them without constraining infra. Backend SA in `lolday` granted a cross-ns Role `backend-jobs` in `lolday-jobs`. See `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` §6.2.
```

- [ ] **Step 3: `docs/runbooks/deploy.md` §9 Maintenance** — append a new sub-section

````markdown
### Migrating vcjobs to the lolday-jobs namespace (one-time, Phase 1)

**Pre-flight (run before `bash scripts/deploy.sh`):**

```bash
bash scripts/migrate-jobs-namespace.sh check
```
````

If any in-flight vcjob is reported, wait for it to finish (or cancel via UI) before deploying. New vcjobs will land in `lolday-jobs`; old in-flight ones in `lolday` continue to run but won't be visible to the new reconciler. Build jobs are similar — in-flight builds finish in `lolday`, new ones go to `lolday-jobs`.

**Deploy:**

```bash
bash scripts/deploy.sh
```

Helm creates the new namespace + quotas + limits + RBAC + NetworkPolicies on first install. Backend `Deployment` rolls with the new `JOB_NAMESPACE / BUILD_NAMESPACE = lolday-jobs` env.

**Post-verify:**

```bash
bash scripts/migrate-jobs-namespace.sh post-verify
bash tests/2026-05-05-jobs-namespace-smoke.sh
```

Submit a small detector evaluate from the UI; confirm the new vcjob lands in `lolday-jobs` (`kubectl get vcjobs -n lolday-jobs`).

**Rollback:**

```bash
helm rollback lolday <prev-rev> -n lolday
```

The `lolday-jobs` namespace is left in place (cheap to keep empty). Any vcjob already created in `lolday-jobs` decays via TTL. Only the `JOB_NAMESPACE` env reverts; new submissions go back to `lolday`.

````

- [ ] **Step 4: `.claude/rules/charts-and-helm.md`** — add a note in the templates section

After the bullet list of top-level templates, add:

```markdown
- **Phase 1 (lolday-jobs ns family)** — `jobs-namespace.yaml`, `jobs-quota.yaml`, `jobs-limitrange.yaml`, `jobs-rbac.yaml`, `lolday-quota.yaml`. These create the dedicated workload namespace + caps. Spec: `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` §6.2.
````

---

## Task 16: pre-commit + commit

- [ ] **Step 1: Stage**

```bash
git add charts/lolday/templates/jobs-namespace.yaml \
  charts/lolday/templates/jobs-quota.yaml \
  charts/lolday/templates/jobs-limitrange.yaml \
  charts/lolday/templates/lolday-quota.yaml \
  charts/lolday/templates/jobs-rbac.yaml \
  charts/lolday/templates/backend-rbac.yaml \
  charts/lolday/templates/backend.yaml \
  charts/lolday/templates/job-networkpolicy.yaml \
  charts/lolday/templates/build-networkpolicy.yaml \
  charts/lolday/values.yaml \
  scripts/migrate-jobs-namespace.sh \
  tests/2026-05-05-jobs-namespace-smoke.sh \
  docs/architecture.md \
  docs/runbooks/deploy.md \
  .claude/rules/charts-and-helm.md \
  docs/superpowers/plans/2026-05-05-gpu-scheduling-phase1-jobs-namespace.md
```

- [ ] **Step 2: pre-commit**

```bash
pre-commit run --files <all the staged files>
```

Re-stage if any auto-fix.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'MSG'
feat(charts): phase 1 — dedicated lolday-jobs namespace + ResourceQuota + LimitRange

Splits detector vcjobs + BuildKit Jobs into a dedicated `lolday-jobs`
namespace so a per-namespace ResourceQuota / LimitRange can cap workload
pods without constraining infra (postgres / prometheus / harbor / etc.).
Closes spec §6.2 (why namespace separation is necessary) + §7 Phase 1.

Resource caps (from spec §7 Phase 1):
- lolday-jobs: requests.memory=30Gi, limits.memory=50Gi, gpu=2, count/pods=16
- lolday-jobs LimitRange: max 16Gi/4cpu, default 4Gi/2cpu, request 1Gi/500m
- lolday infra: requests.memory=20Gi, limits.memory=40Gi (memory-only cap)

Cross-ns RBAC: backend SA in `lolday` gets a second Role `backend-jobs`
in `lolday-jobs`, preserving the Phase 7.5 narrow-scope pattern instead
of widening to a ClusterRole. NetworkPolicies use cross-ns
`namespaceSelector kubernetes.io/metadata.name=lolday` to reach backend
/ mlflow / harbor from `lolday-jobs`.

Migration: backend env JOB_NAMESPACE / BUILD_NAMESPACE switch from
`global.namespace` to new `global.jobsNamespace`. Old finished vcjobs
in `lolday` decay via `ttlSecondsAfterFinished=7d`. Pre-deploy check
in `scripts/migrate-jobs-namespace.sh check` blocks if any in-flight
vcjob exists.

Spec: docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md
Plan: docs/superpowers/plans/2026-05-05-gpu-scheduling-phase1-jobs-namespace.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
MSG
)"
```

---

## Task 17: push + PR

- [ ] **Step 1: push**

```bash
git push -u origin feat/gpu-scheduling-phase1-jobs-namespace
```

- [ ] **Step 2: open PR**

```bash
gh pr create --title "feat(charts): phase 1 — dedicated lolday-jobs namespace + ResourceQuota + LimitRange" --body "..."
```

(Body mirrors the commit message + Test plan checklist.)

---

## Task 18: deploy.sh on server30 (operator-attended)

> Idempotent. No SSH safety hard rule — this is helm, not kubelet. But pre-flight check is still required.

- [ ] **Step 1: Pre-flight**

```bash
bash scripts/migrate-jobs-namespace.sh check
```

Expected: `=== READY FOR DEPLOY ===`. If any in-flight vcjob, wait or cancel.

- [ ] **Step 2: deploy**

```bash
bash scripts/deploy.sh
```

Expected: Helm creates `lolday-jobs` ns + new quotas + RBAC + new NetworkPolicies. Backend pod rolls with new env.

- [ ] **Step 3: Wait for rollout**

```bash
kubectl -n lolday rollout status deploy/backend
```

---

## Task 19: smoke + verify

- [ ] **Step 1: smoke**

```bash
bash tests/2026-05-05-jobs-namespace-smoke.sh
```

Expected: `=== SMOKE PASSED ===`.

- [ ] **Step 2: post-verify**

```bash
bash scripts/migrate-jobs-namespace.sh post-verify
```

- [ ] **Step 3: live test from UI**

Operator submits a small detector evaluate. Confirm:

```bash
kubectl get vcjobs -A
```

Shows the new vcjob in `lolday-jobs`, not `lolday`.

- [ ] **Step 4: rollback path (only if step 1 / 3 fails)**

```bash
helm history lolday -n lolday
helm rollback lolday <prev-rev> -n lolday
```

---

## Out of scope for this plan

- Volcano per-user queue + capability cap — Phase 2 (next PR).
- `ResourceProfile.GPU1` enum + alembic — Phase 3.
- Prometheus alerts (VRAM, NodeMemoryPressure) — Phase 4.
- Per-job `active_deadline_seconds` — Phase 5.

## Self-review checklist (after Wave 0–2, before commit)

- [ ] Every value in jobs-quota.yaml matches §7 Phase 1 of the spec exactly.
- [ ] LimitRange max matches the current detector container limits (16Gi / 4cpu).
- [ ] backend-rbac.yaml retains PVC verbs (samples PVC lives in `lolday`).
- [ ] jobs-rbac.yaml has every verb the same-ns Role had for pods/batch/batch.volcano.sh.
- [ ] NetworkPolicy cross-ns rules use `kubernetes.io/metadata.name=lolday`, not the old same-ns shortcut.
- [ ] `helm lint` clean.
- [ ] `helm template` produces the expected 8 new objects (Namespace × 1, ResourceQuota × 2, LimitRange × 1, Role × 1, RoleBinding × 1, NetworkPolicy × 2).
- [ ] No reference to `global.namespace` in any of the new chart files where `global.jobsNamespace` is meant.
- [ ] Smoke test asserts every value, not "non-empty".
- [ ] Migration runbook prefers root-cause (cap + namespace) over workaround (un-capped legacy).
