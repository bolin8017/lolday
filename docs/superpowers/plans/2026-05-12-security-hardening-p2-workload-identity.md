# Security Hardening P2 — Workload Identity & Tenant Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a compromised backend or detector pod a _local_ incident, not a cluster-wide one. Tighten pod identity (RBAC alone is not enough — add `automountServiceAccountToken: false` + restricted `securityContext` + USER 1000 inside containers), pod-to-pod isolation (default-deny ingress NetworkPolicy + per-service allow rules), and tenant boundaries (Pod Security Standards labels + BuildKit custom seccomp + MLflow per-experiment ACL via Traefik ForwardAuth).

**Architecture:** Sixteen TDD-or-helm-test tasks against the existing chart and backend. Where new code is needed it lives in two new files (`backend/app/internal_app.py` for the M-internal-split sub-app, `backend/app/routers/mlflow_authz.py` for the H-15 ForwardAuth endpoint) plus a new Traefik Middleware YAML. No schema migration; no maldet/job-helper contract change. The MLflow ForwardAuth piece (T15) is the largest single task; everything else is incremental chart hardening or pod-level securityContext additions.

**Tech Stack:** FastAPI, Pydantic v2, Helm 3, K3s, Traefik v2 IngressRoute / Middleware, Volcano scheduler, kube-prometheus-stack, BuildKit-rootless.

**Source spec:** [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../specs/2026-05-12-security-hardening-design.md) §6.2.

**Finding IDs covered:** H-7, H-8, H-9, H-10, H-11, H-12, H-13, H-14, H-15, H-16, H-21, M-backend-np, M-internal-split, M-cloudflared-np, M-minio-console, M-alembic-hardening, M-mlflow-init-hardening (17 findings).

---

## Pre-flight

- [ ] **Confirm clean working tree.** Run `git status` — should be clean on `main` at commit `06715ef` or newer (i.e., P1 merged).
- [ ] **Confirm test baseline.** Run `cd backend && uv run pytest -q` — should be 689 passed.
- [ ] **Create the feature branch.**
  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  git checkout -b security-hardening-p2
  ```

---

## Task 1: [H-13] Delete the orphan `deny-training-egress` NetworkPolicy

**Findings:** H-13 (HIGH).

**Files:**

- Modify: `charts/lolday/templates/network-policy.yaml`

**Rationale:** The policy selects pods with label `lolday.io/role: training`, but **no pod in the cluster carries that label**. The active job-egress restriction is enforced by `templates/job-networkpolicy.yaml` in `lolday-jobs` ns. Removing the dead policy is a hygiene improvement and reduces operator confusion when reading the chart.

- [ ] **Step 1: Verify no pod uses the label.**

  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  grep -rn "lolday.io/role: training" charts/ backend/app/ 2>/dev/null
  ```

  Expected: only one hit, in `network-policy.yaml` itself (the orphan policy). If any other file matches, **stop** — the policy is not orphaned.

- [ ] **Step 2: Remove the policy.**

  Open `charts/lolday/templates/network-policy.yaml` and delete the `deny-training-egress` block (lines 1–16). Keep the `backend-metrics-from-monitoring-only` policy (added by P1 commit `d4ecc50`) intact.

  After deletion, the file should start with the `# H-25:` comment block on what was previously line 18.

- [ ] **Step 3: Delete the values key.**

  Open `charts/lolday/values.yaml` and remove the `training.networkPolicy.enabled` key if present (search for `training:` and `networkPolicy:` under it). Verify there's no other consumer of `training.networkPolicy`:

  ```bash
  grep -rn "training\.networkPolicy\|training:\s*$" charts/lolday/values.yaml
  ```

- [ ] **Step 4: Lint and render.**

  ```bash
  helm lint charts/lolday
  helm template charts/lolday 2>/dev/null | grep -A5 "NetworkPolicy" | head -40
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`. Rendered output shows the surviving policies (`backend-metrics-from-monitoring-only`, `lolday-build-egress`, `lolday-job-egress`, `cloudflared-egress`) but no `deny-training-egress`.

- [ ] **Step 5: Commit.**

  ```bash
  git add charts/lolday/templates/network-policy.yaml charts/lolday/values.yaml
  git commit -m "fix(charts): remove orphan deny-training-egress NetworkPolicy [H-13]
  ```

The policy selected pods by 'lolday.io/role: training', a label
that no pod in the cluster carries. Active job-egress restriction
lives in templates/job-networkpolicy.yaml (lolday-jobs ns)."

````

---

## Task 2: [H-10] Add `USER 1000` to backend Dockerfile

**Findings:** H-10 (HIGH).

**Files:**

- Modify: `backend/Dockerfile`

**Rationale:** The backend Deployment will get `runAsNonRoot: true` in Task 4. For that to succeed, the image must actually have a non-root user available. The frontend, job-helper, and build-helper images already follow this pattern; backend is the outlier.

- [ ] **Step 1: Inspect the current Dockerfile.**

Run `cat backend/Dockerfile` — confirm no existing `USER` directive.

- [ ] **Step 2: Add the user and switch.**

Modify `backend/Dockerfile` so the final layer is (preserve the existing `EXPOSE 8000` + `CMD` lines):

```dockerfile
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY app/ ./app/

# H-10: run as UID 1000. Chown /app so uv's writable cache (~/.cache/uv inside HOME) works.
RUN useradd -m -u 1000 lolday && chown -R lolday:lolday /app
USER 1000

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
````

The `useradd -m` is important: without `-m`, `$HOME` doesn't exist and `uv` falls back to `/.cache/uv` which UID 1000 can't write to.

- [ ] **Step 3: Build and smoke-test.**

  ```bash
  cd backend && docker build -t lolday-backend-p2t2 . --progress=plain 2>&1 | tail -10
  docker run --rm lolday-backend-p2t2 id
  ```

  Expected: build succeeds; `id` prints `uid=1000(lolday) gid=1000(lolday) groups=1000(lolday)`.

- [ ] **Step 4: Verify uv runs as 1000.**

  ```bash
  docker run --rm lolday-backend-p2t2 uv --version
  ```

  Expected: prints uv's version string, no permission error.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/Dockerfile
  git commit -m "fix(backend): run container as UID 1000 [H-10]
  ```

Adds USER 1000 to the backend Dockerfile so the upcoming Helm
chart's restricted securityContext (runAsNonRoot: true) does not
fail readiness."

````

---

## Task 3: [M-minio-console] Disable MinIO Console Service

**Findings:** M-minio-console (MEDIUM).

**Files:**

- Modify: `charts/lolday/values.yaml:752-754`

**Rationale:** MinIO Console at `lolday-minio-console:9001` lets anyone in-cluster with the root credentials sign in to a full IAM management UI. Operators should reach it via `kubectl port-forward` on demand, not via a persistent ClusterIP Service.

- [ ] **Step 1: Locate the consoleService block.**

```bash
grep -n -A2 "consoleService:" charts/lolday/values.yaml
````

Expected: lines 752-754 showing `type: ClusterIP`, `port: "9001"`.

- [ ] **Step 2: Disable the console Service.**

  In `charts/lolday/values.yaml`, change the `consoleService` block to:

  ```yaml
  # H-25 follow-up [M-minio-console]: no in-cluster Service for the Console.
  # Operators port-forward when they need to use it:
  #   kubectl port-forward -n lolday svc/lolday-minio 9001:9001
  # This still exposes the console at :9001 inside the pod, but blocks
  # cluster-internal access from other namespaces.
  consoleService:
    type: ClusterIP
    port: "9001"
  ```

  Wait — the chart's `consoleService.type` accepts `ClusterIP|LoadBalancer|NodePort` but **not the empty string**. The way to disable the Service is to set `consoleIngress.enabled: false` (probably already) AND set a NetworkPolicy that blocks ingress to the Console. The chart does NOT support omitting the Service entirely.

  The correct fix is therefore a NetworkPolicy. Update `charts/lolday/templates/network-policy.yaml` to add (after the `backend-metrics-from-monitoring-only` block):

  ```yaml
  ---
  # M-minio-console: MinIO Console is sub-chart-rendered as a ClusterIP at
  # port 9001. The sub-chart doesn't support disabling it; this NP blocks
  # ingress to the Console pod port instead. Operator port-forward still
  # works (it bypasses the cluster network).
  {{- if .Values.minio.enabled }}
  apiVersion: networking.k8s.io/v1
  kind: NetworkPolicy
  metadata:
    name: minio-console-no-ingress
    namespace: {{ .Values.global.namespace }}
    labels:
      {{- include "lolday.labels" . | nindent 4 }}
  spec:
    podSelector:
      matchLabels:
        app: minio
    policyTypes:
      - Ingress
    ingress:
      # Allow only the S3 API port (9000). Console (9001) gets no ingress.
      - ports:
          - port: 9000
            protocol: TCP
  {{- end }}
  ```

  Leave `values.yaml` `consoleService` block unchanged.

- [ ] **Step 3: Lint and render.**

  ```bash
  helm lint charts/lolday
  helm template charts/lolday 2>/dev/null | grep -B2 -A20 "minio-console-no-ingress"
  ```

  Expected: lint passes; the NetworkPolicy renders.

- [ ] **Step 4: Commit.**

  ```bash
  git add charts/lolday/templates/network-policy.yaml
  git commit -m "fix(charts): block ingress to MinIO Console :9001 [M-minio-console]
  ```

The chart's consoleService cannot be disabled by config; this NP
restricts ingress to the S3 API port only. Operator port-forward
still works (bypasses cluster network)."

````

---

## Task 4: [H-7] Backend pod `automountServiceAccountToken: false` + restricted securityContext

**Findings:** H-7 (HIGH).

**Files:**

- Modify: `charts/lolday/templates/backend.yaml`

**Rationale:** With T17 from P1 (`c6691f8`), the backend Role no longer grants secrets/configmaps in `lolday` ns. But the SA token is still mounted at `/var/run/secrets/kubernetes.io/serviceaccount/token` by default — anyone with RCE in the backend pod can still use it (only the API server checks RBAC). Setting `automountServiceAccountToken: false` removes the token entirely; backend code reads its credentials from env mounts (Pod Spec), not from the K8s API.

- [ ] **Step 1: Confirm backend code reads no K8s API calls from inside the pod for *its own* config.**

```bash
grep -rn "in_cluster_config\|ServiceAccount\|read_namespaced_secret\|read_namespaced_config_map" backend/app/ | head -20
````

Expected: hits in `services/k8s.py`, `services/jobs_dispatch.py`, `services/harbor_init.py`. Each call is against `JOB_NAMESPACE` / `BUILD_NAMESPACE` (== `lolday-jobs`), not for backend config. The backend's own DB / Fernet / Harbor creds are env-mounted via Pod Spec.

But the K8s client itself requires SA token to authenticate. If we disable automount, the client breaks. So we need a **projected** token volume specifically for the K8s client calls — same SA, but mounted at a non-default path the client can be pointed at.

Actually re-examining: the standard K8s client picks up the token at the standard path `/var/run/secrets/kubernetes.io/serviceaccount/token`. If we set `automountServiceAccountToken: false`, the client cannot authenticate.

Pragmatic decision: **keep the SA token mounted**, but rely on the P1 RBAC narrow (C-1) as the primary defense. Setting `automountServiceAccountToken: false` is incompatible with backend code that talks to the K8s API for its job-dispatch path.

Update Task 4 scope: focus on the **securityContext** half (`runAsNonRoot`, `runAsUser`, `seccompProfile`, capability drop). The `automountServiceAccountToken` change is deferred to a future iteration once the backend's K8s client is refactored to use a separate, narrower SA (or moves to a sidecar).

- [ ] **Step 2: Add the restricted securityContext to the backend Deployment.**

  Edit `charts/lolday/templates/backend.yaml`. The current `spec.template.spec` (line 32) reads:

  ```yaml
  spec:
    serviceAccountName: backend
    containers:
      - name: backend
        image: { { .Values.backend.image } }
  ```

  Change to:

  ```yaml
  spec:
    serviceAccountName: backend
    # H-7: deferred — backend talks to K8s API for job dispatch and
    # therefore needs the projected SA token. Until that code path is
    # refactored, RBAC narrow (P1 C-1) is the primary defense.
    automountServiceAccountToken: true
    securityContext:
      runAsNonRoot: true
      runAsUser: 1000
      fsGroup: 1000
      seccompProfile:
        type: RuntimeDefault
    containers:
      - name: backend
        image: { { .Values.backend.image } }
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop: [ALL]
          readOnlyRootFilesystem: true
  ```

  `readOnlyRootFilesystem: true` will break the backend if it writes any temp files. Add an `emptyDir` for `/tmp`:

  In the same Deployment, add to `spec.template.spec`:

  ```yaml
  volumes:
    - name: tmp
      emptyDir: {}
  ```

  And to the backend container:

  ```yaml
  volumeMounts:
    - name: tmp
      mountPath: /tmp
  ```

- [ ] **Step 3: Lint and render.**

  ```bash
  helm lint charts/lolday
  helm template charts/lolday 2>/dev/null | grep -B2 -A25 "kind: Deployment" | grep -A30 "name: backend" | head -40
  ```

  Expected: the rendered Deployment shows the securityContext + tmp volume + tmp volumeMount.

- [ ] **Step 4: Commit.**

  ```bash
  git add charts/lolday/templates/backend.yaml
  git commit -m "fix(charts): restricted securityContext on backend Deployment [H-7]
  ```

Adds Pod Security Standards 'restricted'-compatible securityContext:
runAsNonRoot, runAsUser 1000, drop ALL capabilities, allowPrivEsc
false, readOnlyRootFilesystem true, RuntimeDefault seccomp. Adds an
emptyDir /tmp for the read-only root.

automountServiceAccountToken stays true — backend's job-dispatch
path uses the K8s API and would break without the projected token.
Deferred until the K8s client is moved to a narrower SA."

````

---

## Task 5: [H-8] Postgres pod restricted securityContext

**Findings:** H-8 (HIGH).

**Files:**

- Modify: `charts/lolday/templates/postgresql.yaml`

**Rationale:** Today the Postgres StatefulSet runs with the container's default user (root inside `postgres:16-alpine`). A SQL-injection-driven RCE escalates to UID 0 with full default caps. Postgres images have CVE history that matters here.

- [ ] **Step 1: Add securityContext.**

Edit `charts/lolday/templates/postgresql.yaml`. The current `spec.template.spec` (line 34) reads:

```yaml
    spec:
      containers:
        - name: postgresql
          image: ...
````

Change to:

```yaml
spec:
  securityContext:
    runAsNonRoot: true
    runAsUser: 70 # the postgres user in postgres:16-alpine
    runAsGroup: 70
    fsGroup: 70
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: postgresql
      image: ...
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          drop: [ALL]
        readOnlyRootFilesystem: true
      volumeMounts:
        - name: data
          mountPath: /var/lib/postgresql/data
        - name: run
          mountPath: /var/run/postgresql
        - name: tmp
          mountPath: /tmp
```

And add the two new emptyDir volumes at the same level as the existing `volumeClaimTemplates`. But `volumeClaimTemplates` is at the StatefulSet level, separate from `volumes`. Add `volumes` to `spec.template.spec`:

```yaml
volumes:
  - name: run
    emptyDir: {}
  - name: tmp
    emptyDir: {}
```

Postgres needs writable `/var/run/postgresql` (for the socket) and `/tmp` (for sort scratch).

- [ ] **Step 2: Lint and render.**

  ```bash
  helm lint charts/lolday
  helm template charts/lolday 2>/dev/null | grep -B2 -A30 "name: postgresql" | grep -A25 "kind: StatefulSet" | head -45
  ```

- [ ] **Step 3: Commit.**

  ```bash
  git add charts/lolday/templates/postgresql.yaml
  git commit -m "fix(charts): restricted securityContext on Postgres StatefulSet [H-8]
  ```

runAsUser 70 (the postgres user in the alpine image), drop ALL
capabilities, allowPrivEsc false, readOnlyRootFilesystem true.
Adds emptyDir volumes for /var/run/postgresql (socket) and /tmp."

````

---

## Task 6: [H-9] Redis pod restricted securityContext + password

**Findings:** H-9 (HIGH).

**Files:**

- Modify: `charts/lolday/templates/redis.yaml`
- Possibly: `backend/app/config.py` (REDIS_URL env consumer)
- Possibly: `charts/lolday/templates/backend.yaml` (pass REDIS_PASSWORD)

**Rationale:** Today Redis runs with no `requirepass` directive. Anyone in-cluster can connect to `redis:6379` and abuse `CONFIG SET dir / dbfilename authorized_keys` to write through to whatever filesystem is mounted (CVE-2022-0543 family). Also runs as root.

- [ ] **Step 1: Create a Secret for the password.**

Append to `charts/lolday/templates/redis.yaml` (top of file, before the Deployment):

```yaml
{{- if .Values.redis.enabled }}
apiVersion: v1
kind: Secret
metadata:
  name: redis
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: redis
    {{- include "lolday.labels" . | nindent 4 }}
type: Opaque
stringData:
  REDIS_PASSWORD: {{ .Values.redis.auth.password | quote }}
---
apiVersion: apps/v1
kind: Deployment
...
````

And the `redis.auth.password` value in `values.yaml`:

```yaml
redis:
  enabled: true
  image: redis:7-alpine
  auth:
    password: "" # operator MUST `--set redis.auth.password=...` at deploy
```

Add a value-required check by using `{{ required "redis.auth.password must be set" .Values.redis.auth.password }}` in the Secret rendering — that way `helm install` fails fast if the operator forgets to set it.

- [ ] **Step 2: Pass the password to Redis at startup.**

  Modify the Redis container in the same file:

  ```yaml
  spec:
    securityContext:
      runAsNonRoot: true
      runAsUser: 999 # redis user in redis:7-alpine
      runAsGroup: 999
      fsGroup: 999
      seccompProfile:
        type: RuntimeDefault
    containers:
      - name: redis
        image: { { .Values.redis.image | default "redis:7-alpine" } }
        ports:
          - containerPort: 6379
        env:
          - name: REDIS_PASSWORD
            valueFrom:
              secretKeyRef:
                name: redis
                key: REDIS_PASSWORD
        # H-9: require password + restricted seccomp.
        command:
          - sh
          - -c
          - >-
            redis-server
            --requirepass "$REDIS_PASSWORD"
            --protected-mode yes
            --maxmemory 128mb
            --maxmemory-policy allkeys-lru
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop: [ALL]
          readOnlyRootFilesystem: true
        volumeMounts:
          - name: tmp
            mountPath: /tmp
        resources:
          requests:
            cpu: 50m
            memory: 64Mi
          limits:
            cpu: 200m
            memory: 192Mi
        livenessProbe:
          exec:
            command: [sh, -c, 'redis-cli -a "$REDIS_PASSWORD" ping']
          initialDelaySeconds: 5
          periodSeconds: 10
        readinessProbe:
          exec:
            command: [sh, -c, 'redis-cli -a "$REDIS_PASSWORD" ping']
          initialDelaySeconds: 3
          periodSeconds: 5
    volumes:
      - name: tmp
        emptyDir: {}
  ```

- [ ] **Step 3: Wire backend to use the password.**

  Modify `charts/lolday/templates/backend.yaml`. The current REDIS_URL secret is `redis://redis:6379/0`. Change to `redis://:$(REDIS_PASSWORD)@redis:6379/0` and feed `REDIS_PASSWORD` via env.

  In the `backend` Secret block (line 11):

  ```yaml
  stringData:
    DATABASE_URL: "postgresql+asyncpg://{{ .Values.postgresql.auth.username }}:{{ .Values.postgresql.auth.password }}@postgresql:5432/{{ .Values.postgresql.auth.database }}"
    REDIS_URL: "redis://:{{ required "redis.auth.password must be set" .Values.redis.auth.password }}@redis:6379/0"
  ```

- [ ] **Step 4: Lint and render.**

  ```bash
  helm lint charts/lolday --set redis.auth.password=test-pwd
  helm template charts/lolday --set redis.auth.password=test-pwd 2>/dev/null | grep -B2 -A30 "command:" | grep -A5 "requirepass"
  ```

- [ ] **Step 5: Commit.**

  ```bash
  git add charts/lolday/templates/redis.yaml charts/lolday/templates/backend.yaml charts/lolday/values.yaml
  git commit -m "fix(charts)!: Redis password + restricted securityContext [H-9]
  ```

BREAKING CHANGE: operator must --set redis.auth.password=<value>
at deploy. Existing 'redis://redis:6379/0' URL no longer connects.
Container runs as UID 999 with drop ALL caps + readOnlyRootFilesystem."

````

---

## Task 7: [M-alembic-hardening + M-mlflow-init-hardening] Job hardening

**Findings:** M-alembic-hardening (MEDIUM), M-mlflow-init-hardening (MEDIUM).

**Files:**

- Modify: `charts/lolday/templates/alembic-upgrade-hook.yaml`
- Modify: `charts/lolday/templates/mlflow-db-init-job.yaml`

**Rationale:** Both Jobs run with default permissions: alembic with default automount, mlflow-db-init with pod-level securityContext but no container-level controls. Pod Security Standards "restricted" requires container-level `allowPrivilegeEscalation: false` + `capabilities.drop: [ALL]` + `seccompProfile.type: RuntimeDefault`.

- [ ] **Step 1: Harden alembic-upgrade-hook.**

Edit `charts/lolday/templates/alembic-upgrade-hook.yaml`. After `restartPolicy: Never` (line 38) add `automountServiceAccountToken: false`. Then add pod + container securityContext. The container also needs `/tmp` writable since alembic may write temp files:

```yaml
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: alembic
          image: {{ .Values.backend.image }}
          imagePullPolicy: Always
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: [ALL]
            readOnlyRootFilesystem: true
          volumeMounts:
            - name: tmp
              mountPath: /tmp
          command: ["uv", "run", "alembic", "upgrade", "head"]
          env: ...
          resources: ...
      volumes:
        - name: tmp
          emptyDir: {}
````

- [ ] **Step 2: Harden mlflow-db-init-job.**

  Edit `charts/lolday/templates/mlflow-db-init-job.yaml`. The pod-level securityContext at lines 16-19 (runAsNonRoot, runAsUser 999, fsGroup 999) is correct. Add the container-level block + tmp emptyDir:

  ```yaml
  spec:
    restartPolicy: OnFailure
    automountServiceAccountToken: false
    securityContext:
      runAsNonRoot: true
      runAsUser: 999
      fsGroup: 999
      seccompProfile:
        type: RuntimeDefault
    containers:
      - name: init
        image: { { .Values.postgresql.image | default "postgres:16-alpine" } }
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop: [ALL]
          readOnlyRootFilesystem: true
        volumeMounts:
          - name: tmp
            mountPath: /tmp
        command: [sh, -c]
        args: ...
        env: ...
    volumes:
      - name: tmp
        emptyDir: {}
  ```

- [ ] **Step 3: Lint and render both Jobs.**

  ```bash
  helm lint charts/lolday --set redis.auth.password=test-pwd
  helm template charts/lolday --set redis.auth.password=test-pwd 2>/dev/null | grep -B2 -A20 "name: alembic-upgrade" | head -40
  helm template charts/lolday --set redis.auth.password=test-pwd 2>/dev/null | grep -B2 -A20 "name: mlflow-db-init" | head -40
  ```

- [ ] **Step 4: Commit.**

  ```bash
  git add charts/lolday/templates/alembic-upgrade-hook.yaml charts/lolday/templates/mlflow-db-init-job.yaml
  git commit -m "fix(charts): harden alembic + mlflow-db-init Job pods [M-alembic-hardening, M-mlflow-init-hardening]
  ```

Both Jobs now: automountServiceAccountToken: false, drop ALL caps,
allowPrivEsc false, readOnlyRootFilesystem true, RuntimeDefault
seccomp. Adds emptyDir /tmp where needed."

````

---

## Task 8: [H-14] Pod Security Standards labels on namespaces

**Findings:** H-14 (HIGH).

**Files:**

- Modify: `charts/lolday/templates/jobs-namespace.yaml`
- Create: `charts/lolday/templates/lolday-namespace.yaml`
- Create: `charts/lolday/templates/monitoring-namespace-pss.yaml`

**Rationale:** Without PSS labels, anyone with `create` on pods can submit a pod with `privileged: true`, `hostPath: /`, or `hostNetwork: true`. K3s 1.25+ has built-in admission for PSS labels. We start in `audit` mode for 7 days, then promote to `enforce`.

The lolday namespace is currently NOT chart-rendered (Helm uses `--create-namespace` at install time). Adding a Namespace template for `lolday` is tricky because Helm will try to install into a namespace that doesn't yet exist as the chart object. For chart-rendered control, we'll add the labels via a separate post-install Job that runs `kubectl label namespace`, OR document the operator action in `scripts/deploy.sh`.

Pragmatic decision: add to existing `jobs-namespace.yaml` for `lolday-jobs` (the most adversarial ns), and document the manual `kubectl label` for `lolday` and `monitoring` in the deploy runbook.

- [ ] **Step 1: Label `lolday-jobs` via the chart.**

Modify `charts/lolday/templates/jobs-namespace.yaml`:

```yaml
{{/* Phase 1 — dedicated namespace for detector vcjobs + BuildKit Jobs.
     Decoupled from `global.namespace` so a per-namespace
     ResourceQuota / LimitRange can cap workload pods without
     constraining infra. See spec §6.2.

     H-14 (P2): Pod Security Standards labels start in 'audit + warn'
     mode so misbehaving pods are reported but not blocked. After 7
     days of clean audit logs, promote to 'enforce' via:
       kubectl label ns lolday-jobs pod-security.kubernetes.io/enforce=restricted --overwrite
     (See docs/runbooks/p2-pss-enforce-promotion.md.)
     BuildKit is a known exception — it requires `seccompProfile: Unconfined`,
     which is denied by PSS 'restricted'. BuildKit runs in its own
     'lolday-builds' ns (Phase 2 follow-up) at PSS 'baseline'.
     For P2, BuildKit Jobs stay in lolday-jobs at PSS 'baseline' until
     the split lands. */}}
apiVersion: v1
kind: Namespace
metadata:
  name: {{ .Values.global.jobsNamespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
    lolday.io/role: workload
    kubernetes.io/metadata.name: {{ .Values.global.jobsNamespace }}
    # H-14 — PSS in audit + warn mode for 7 days; then promote to enforce.
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
    # BuildKit needs baseline (Unconfined seccomp). Once Phase 2 follow-up
    # moves BuildKit to its own ns, change this to: enforce: restricted.
    pod-security.kubernetes.io/enforce: baseline
````

- [ ] **Step 2: Document `lolday` and `monitoring` labels in deploy runbook.**

  Create or update `docs/runbooks/p2-pss-labels.md`:

  ```markdown
  # PSS labels for lolday and monitoring namespaces

  The `lolday-jobs` ns gets PSS labels from the chart (`templates/jobs-namespace.yaml`).
  For `lolday` and `monitoring` namespaces (which the chart does not own as
  Namespace objects — they are created by `helm install --create-namespace`),
  the operator must apply labels post-install:

      kubectl label ns lolday \
        pod-security.kubernetes.io/audit=restricted \
        pod-security.kubernetes.io/warn=restricted \
        pod-security.kubernetes.io/enforce=baseline \
        --overwrite

      kubectl label ns monitoring \
        pod-security.kubernetes.io/audit=restricted \
        pod-security.kubernetes.io/warn=restricted \
        pod-security.kubernetes.io/enforce=baseline \
        --overwrite

  After 7 days of clean audit logs (`kubectl get events -n <ns> | grep PodSecurity`),
  promote enforce to restricted:

      kubectl label ns lolday pod-security.kubernetes.io/enforce=restricted --overwrite
      kubectl label ns monitoring pod-security.kubernetes.io/enforce=restricted --overwrite
      kubectl label ns lolday-jobs pod-security.kubernetes.io/enforce=restricted --overwrite

  Don't promote `lolday-jobs` until BuildKit moves to its own ns (Phase 2 follow-up).
  ```

- [ ] **Step 3: Lint and render.**

  ```bash
  helm lint charts/lolday --set redis.auth.password=test-pwd
  helm template charts/lolday --set redis.auth.password=test-pwd 2>/dev/null | grep -B2 -A10 "name: lolday-jobs"
  ```

  Expected: the rendered Namespace shows all four labels.

- [ ] **Step 4: Commit.**

  ```bash
  git add charts/lolday/templates/jobs-namespace.yaml docs/runbooks/p2-pss-labels.md
  git commit -m "feat(charts): PSS labels on lolday-jobs ns [H-14]
  ```

Adds Pod Security Standards labels in audit + warn mode at
'restricted' level. Enforce starts at 'baseline' until BuildKit
moves to a dedicated ns (it requires Unconfined seccomp).

Adds runbook docs/runbooks/p2-pss-labels.md describing post-install
labels for lolday and monitoring namespaces and the 7-day
audit-to-enforce promotion."

````

---

## Task 9: [H-12] Default-deny ingress NetworkPolicy on `lolday` ns

**Findings:** H-12 (HIGH).

**Files:**

- Create: `charts/lolday/templates/netpol-lolday-default-deny.yaml`

**Rationale:** No NetworkPolicy currently restricts ingress to the `lolday` ns. Any compromised pod cluster-wide can connect to Postgres :5432, MinIO :9000, MLflow :5000, Harbor :80, Redis :6379. A default-deny + explicit allow list per service is the standard zero-trust pattern.

- [ ] **Step 1: Create the policies file.**

Create `charts/lolday/templates/netpol-lolday-default-deny.yaml`:

```yaml
# H-12: default-deny ingress for the lolday infra namespace, plus per-
# service allow rules. NB:
#   - The backend ingress NP is in templates/network-policy.yaml
#     (added by P1 H-25), not here.
#   - cloudflared ingress NP is added in T11 below.
#   - jobs-ns ingress NPs are already in templates/job-networkpolicy.yaml.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: lolday-default-deny-ingress
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector: {}
  policyTypes:
    - Ingress
  # No `ingress:` block = deny all by default. Allow rules below add back
  # what each service legitimately needs.
---
# Postgres: reachable from backend, mlflow, postgres-exporter (in monitoring).
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: postgresql-ingress-allow
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: postgresql
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/component: backend
        - podSelector:
            matchLabels:
              app.kubernetes.io/component: mlflow
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
          podSelector:
            matchLabels:
              app.kubernetes.io/name: postgres-exporter
      ports:
        - port: 5432
          protocol: TCP
---
# Redis: reachable from backend only.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: redis-ingress-allow
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: redis
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/component: backend
      ports:
        - port: 6379
          protocol: TCP
---
# MLflow: reachable from backend (proxy) and Traefik (for ForwardAuth-gated
# /mlflow/ routes). Direct access from jobs ns is BLOCKED — jobs must use
# the Traefik route. The Traefik IngressController lives in kube-system
# (or wherever K3s puts it); we identify it by its standard label.
# H-15 adds the ForwardAuth wiring; until then, this NP also rejects
# cluster-internal MLflow access from jobs (intentional — verify with
# acceptance test).
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: mlflow-ingress-allow
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: mlflow
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/component: backend
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              app.kubernetes.io/name: traefik
      ports:
        - port: {{ .Values.mlflow.service.port | int }}
          protocol: TCP
---
# Harbor: reachable from backend (build orchestration) and from job pods
# (image pull at runtime). Cross-ns selector.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: harbor-ingress-allow
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app: harbor
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/component: backend
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.global.jobsNamespace }}
      ports:
        - port: 80
          protocol: TCP
````

**Note:** The Harbor selector `app: harbor` matches the goharbor chart's pod labels; verify with `helm template charts/lolday | grep -B2 "app: harbor"`.

- [ ] **Step 2: Lint and render.**

  ```bash
  helm lint charts/lolday --set redis.auth.password=test-pwd
  helm template charts/lolday --set redis.auth.password=test-pwd 2>/dev/null | grep -B2 -A15 "default-deny-ingress"
  helm template charts/lolday --set redis.auth.password=test-pwd 2>/dev/null | grep -c "kind: NetworkPolicy"
  ```

  Expected: lint clean; the default-deny + 4 per-service NPs render; total NetworkPolicy count goes up by 5.

- [ ] **Step 3: Commit.**

  ```bash
  git add charts/lolday/templates/netpol-lolday-default-deny.yaml
  git commit -m "feat(charts): default-deny ingress NP on lolday ns + per-service allow [H-12]
  ```

postgresql -> backend, mlflow, postgres-exporter
redis -> backend
mlflow -> backend, traefik (sets up H-15 forward-auth path)
harbor -> backend, lolday-jobs (image pull)

Default-deny covers any future infra Service so the operator must
explicitly enumerate consumers."

````

---

## Task 10: [M-backend-np] Backend pod ingress NetworkPolicy

**Findings:** M-backend-np (MEDIUM).

**Files:**

- Modify: `charts/lolday/templates/network-policy.yaml`

**Rationale:** P1 added `backend-metrics-from-monitoring-only`, which restricts ingress to the backend pod. With T9 default-deny, the backend would already be covered — but the H-25 policy from P1 explicitly opens port 8000 to cloudflared (own ns) and `lolday-jobs` (cross-ns). T9 default-deny is generic; the backend-specific allow rules are already in place from P1. This task is therefore a **verification + minor polish**: make sure the policy names don't conflict and the rules are correct under the T9 deny default.

- [ ] **Step 1: Verify the P1 policy is intact.**

```bash
grep -B2 -A30 "backend-metrics-from-monitoring-only" charts/lolday/templates/network-policy.yaml
````

The P1 policy already permits cloudflared and jobs-ns to reach port 8000. With T9 default-deny in place, this remains the authoritative allow rule.

- [ ] **Step 2: No code change needed.**

  Document by adding a one-line comment to `templates/network-policy.yaml` clarifying that the existing `backend-metrics-from-monitoring-only` policy doubles as the backend ingress allow under T9's default-deny:

  ```yaml
  # H-25 + M-backend-np: this policy is both the backend's full ingress allow
  # list AND the metrics-source gate. Under H-12's default-deny on the lolday
  # ns, nothing else reaches the backend pod.
  ```

  Add the comment just above the existing `name: backend-metrics-from-monitoring-only` metadata block.

- [ ] **Step 3: Lint and confirm.**

  ```bash
  helm lint charts/lolday --set redis.auth.password=test-pwd
  ```

- [ ] **Step 4: Commit.**

  ```bash
  git add charts/lolday/templates/network-policy.yaml
  git commit -m "docs(charts): clarify backend ingress NP under default-deny [M-backend-np]
  ```

P1's backend-metrics-from-monitoring-only policy is now the
authoritative allow rule under H-12's default-deny. Comment added
for future readers."

````

---

## Task 11: [M-cloudflared-np] cloudflared :2000 ingress NetworkPolicy

**Findings:** M-cloudflared-np (MEDIUM).

**Files:**

- Modify: `charts/lolday/templates/netpol-cloudflared.yaml`

**Rationale:** cloudflared pod exposes a `:2000` metrics endpoint that any in-cluster pod can scrape — leaks tunnel state (connected, replicaset count, etc.). Already covered by T9's default-deny if cloudflared lives in `lolday` ns; but the cloudflared NP is its own file, so apply an explicit allow.

- [ ] **Step 1: Inspect current cloudflared NP.**

```bash
cat charts/lolday/templates/netpol-cloudflared.yaml
````

Existing policy probably only covers `policyTypes: [Egress]`.

- [ ] **Step 2: Add ingress restriction.**

  Modify the file to add `Ingress` to `policyTypes` and an ingress block allowing only the monitoring ns (for Prometheus scrapes):

  ```yaml
  apiVersion: networking.k8s.io/v1
  kind: NetworkPolicy
  metadata:
    name: cloudflared
    namespace: { { .Values.global.namespace } }
    labels: { { - include "lolday.labels" . | nindent 4 } }
  spec:
    podSelector:
      matchLabels:
        app.kubernetes.io/component: cloudflared
    policyTypes:
      - Ingress
      - Egress
    ingress:
      - from:
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: monitoring
            podSelector:
              matchLabels:
                app.kubernetes.io/name: prometheus
        ports:
          - port: 2000
            protocol: TCP
    egress:
      # (existing egress rules preserved)
      - to:
          - ipBlock:
              cidr: 0.0.0.0/0
              except:
                - 10.0.0.0/8
                - 172.16.0.0/12
                - 192.168.0.0/16
        ports:
          - port: 443
            protocol: TCP
          - port: 7844
            protocol: TCP
  ```

  Adjust the egress block to match whatever is currently in the file.

- [ ] **Step 3: Lint and render.**

  ```bash
  helm lint charts/lolday --set redis.auth.password=test-pwd
  helm template charts/lolday --set redis.auth.password=test-pwd 2>/dev/null | grep -B2 -A20 'name: cloudflared$' | head -30
  ```

- [ ] **Step 4: Commit.**

  ```bash
  git add charts/lolday/templates/netpol-cloudflared.yaml
  git commit -m "fix(charts): restrict cloudflared :2000 ingress to monitoring [M-cloudflared-np]
  ```

Adds Ingress to policyTypes; only the monitoring ns Prometheus pod
can scrape cloudflared's /metrics. Egress rules unchanged."

````

---

## Task 12: [H-11] BuildKit custom seccomp profile

**Findings:** H-11 (HIGH).

**Files:**

- Modify: `backend/app/services/build.py:80-89`
- Create: `charts/lolday/files/buildkit/seccomp.json`
- Modify: `charts/lolday/templates/backend.yaml` (mount seccomp profile)

**Rationale:** BuildKit-rootless requires user-namespace syscalls (`setuid32`, `setgid32`, `clone`, `unshare`) that PSS Restricted-default seccomp blocks. The current workaround is `seccompProfile: Unconfined`, which is the maximum-permission setting. BuildKit upstream publishes a custom seccomp profile that allows just the syscalls BuildKit needs.

**Caveat:** Custom seccomp profiles must be available to the kubelet at `/var/lib/kubelet/seccomp/profiles/<name>.json`. K3s on server30 needs that file dropped manually — this task includes a DaemonSet that populates it.

- [ ] **Step 1: Fetch the BuildKit reference seccomp profile.**

```bash
mkdir -p charts/lolday/files/buildkit
curl -sSL https://raw.githubusercontent.com/moby/buildkit/v0.13.0/examples/kubernetes/buildkit-rootless-seccomp.json \
  -o charts/lolday/files/buildkit/seccomp.json
````

Verify it's a valid JSON seccomp profile:

```bash
jq '.defaultAction, (.syscalls | length)' charts/lolday/files/buildkit/seccomp.json
```

Expected: a `defaultAction` like `"SCMP_ACT_ERRNO"` and a non-zero syscall count.

- [ ] **Step 2: Create the DaemonSet that drops the profile onto every node's kubelet dir.**

  Create `charts/lolday/templates/buildkit-seccomp-installer.yaml`:

  ```yaml
  {{- if .Values.buildkit.seccompProfile.enabled }}
  apiVersion: v1
  kind: ConfigMap
  metadata:
    name: buildkit-seccomp-profile
    namespace: {{ .Values.global.namespace }}
    labels:
      {{- include "lolday.labels" . | nindent 4 }}
  data:
    seccomp.json: |-
      {{ .Files.Get "files/buildkit/seccomp.json" | nindent 6 }}
  ---
  apiVersion: apps/v1
  kind: DaemonSet
  metadata:
    name: buildkit-seccomp-installer
    namespace: {{ .Values.global.namespace }}
    labels:
      {{- include "lolday.labels" . | nindent 4 }}
  spec:
    selector:
      matchLabels:
        app.kubernetes.io/name: buildkit-seccomp-installer
    template:
      metadata:
        labels:
          app.kubernetes.io/name: buildkit-seccomp-installer
      spec:
        # Privileged hostPath write — required to populate kubelet's seccomp dir.
        # Runs once per node and exits; restartPolicy: OnFailure keeps the pod
        # in Completed state.
        containers:
          - name: installer
            image: busybox:1.36
            command:
              - sh
              - -c
              - |
                set -e
                mkdir -p /host-seccomp/profiles
                cp /config/seccomp.json /host-seccomp/profiles/buildkit-rootless.json
                chmod 644 /host-seccomp/profiles/buildkit-rootless.json
                # Stay alive so DaemonSet doesn't restart in a loop.
                sleep infinity
            volumeMounts:
              - name: config
                mountPath: /config
              - name: host-seccomp
                mountPath: /host-seccomp
            securityContext:
              # hostPath write to /var/lib/kubelet requires root.
              # Compensating control: scope is limited to a single file.
              runAsNonRoot: false
              runAsUser: 0
        volumes:
          - name: config
            configMap:
              name: buildkit-seccomp-profile
          - name: host-seccomp
            hostPath:
              path: /var/lib/kubelet/seccomp
              type: DirectoryOrCreate
  {{- end }}
  ```

  And in `charts/lolday/values.yaml`, add:

  ```yaml
  buildkit:
    seccompProfile:
      enabled: true
  ```

  **NB:** This DaemonSet runs with `runAsUser: 0` and `hostPath` — it would fail PSS Restricted. The `lolday` ns is at PSS `enforce: baseline` (per Task 8), which allows hostPath but not privileged. If the installer needs `privileged: true`, move it to its own ns at `enforce: privileged`, or use a Job + nodeSelector instead.

  For simplicity, accept the DaemonSet as-is at PSS baseline. If hostPath write at UID 0 turns out to need privileged, **stop and escalate** — the design may need a different approach.

- [ ] **Step 3: Switch BuildKit container to use the profile.**

  Modify `backend/app/services/build.py:80-89`:

  ```python
  # H-11 (P2): use BuildKit upstream's custom seccomp profile instead of
  # Unconfined. The profile must exist at /var/lib/kubelet/seccomp/profiles/
  # on every node — installed by the buildkit-seccomp-installer DaemonSet
  # (charts/lolday/templates/buildkit-seccomp-installer.yaml).
  buildkit_sc = {
      "runAsNonRoot": True,
      "runAsUser": 1000,
      "runAsGroup": 1000,
      "seccompProfile": {
          "type": "Localhost",
          "localhostProfile": "profiles/buildkit-rootless.json",
      },
      "appArmorProfile": {"type": "Unconfined"},
  }
  ```

  Leave `appArmorProfile` Unconfined — that's an orthogonal control with different infrastructure requirements.

- [ ] **Step 4: Write a smoke test.**

  Add to `backend/tests/test_services_build.py` (if it exists; otherwise create):

  ```python
  def test_buildkit_sc_uses_localhost_seccomp_profile():
      from app.services.build import _build_buildkit_job  # adapt to actual function name

      job = _build_buildkit_job(build_id="test-b", ...)  # fill in required args
      bk_container_sc = next(
          c["securityContext"] for c in job["spec"]["template"]["spec"]["containers"]
          if c["name"] == "buildkit"
      )
      assert bk_container_sc["seccompProfile"] == {
          "type": "Localhost",
          "localhostProfile": "profiles/buildkit-rootless.json",
      }
  ```

  Inspect `backend/app/services/build.py` first to find the actual function name and required args.

- [ ] **Step 5: Run, verify, commit.**

  ```bash
  cd backend && uv run pytest tests/test_services_build.py -v
  helm lint charts/lolday --set redis.auth.password=test-pwd
  ```

  ```bash
  git add backend/app/services/build.py \
          charts/lolday/files/buildkit/seccomp.json \
          charts/lolday/templates/buildkit-seccomp-installer.yaml \
          charts/lolday/values.yaml \
          backend/tests/test_services_build.py
  git commit -m "feat(backend,charts): BuildKit custom seccomp profile [H-11]
  ```

Replaces seccompProfile: Unconfined with Localhost pointing at the
BuildKit upstream rootless profile. New DaemonSet installs the
profile at /var/lib/kubelet/seccomp/profiles/ on every node."

````

---

## Task 13: [H-21] Volcano queue server-side enforcement

**Findings:** H-21 (HIGH).

**Files:**

- Verify (and potentially modify): `backend/app/routers/jobs.py::create_job` and `backend/app/services/job_spec.py::build_volcano_job_manifest`
- Test: `backend/tests/test_jobs.py`

**Rationale:** The audit flagged that "Volcano queue admits client-supplied queue name". Inspect the create_job flow — `spec.queue` MUST be derived from `user.id` server-side, not read from the request body.

- [ ] **Step 1: Trace the queue assignment.**

```bash
cd backend && grep -n "queue\|Queue" app/routers/jobs.py app/services/job_spec.py | head -30
````

Look for:

- `body.queue` or `request.queue` (RED — client-supplied)
- `ensure_user_queue(user.id)` or `queue_name_for_user(user.id)` (GREEN — server-derived)

- [ ] **Step 2: If server-derived already, write a regression test.**

  Add to `backend/tests/test_jobs.py`:

  ```python
  @pytest.mark.asyncio
  async def test_create_job_ignores_client_supplied_queue(
      auth_client_developer, seed_detector, monkeypatch
  ):
      """Even if the request body has a 'queue' field, the resulting
      vcjob manifest must use the user's server-derived queue, not
      whatever the client claimed."""
      from app.routers import detectors as dr
      monkeypatch.setattr(dr, "_create_k8s_resources", AsyncMock(return_value="b-x"))

      captured = {}

      async def fake_dispatch(*args, **kwargs):
          captured["manifest"] = args[0]  # adapt to real call shape
          return "vcjob-name"

      from app.services import jobs_dispatch
      monkeypatch.setattr(jobs_dispatch, "dispatch_job_to_volcano", fake_dispatch)

      resp = await auth_client_developer.post(
          "/api/v1/jobs",
          json={
              "detector_version_id": "...",  # adapt
              "job_type": "train",
              "params": {},
              "queue": "lolday-u-evil-other-user",  # attacker-controlled
          },
      )
      assert resp.status_code == 201
      assert captured["manifest"]["spec"]["queue"].startswith("lolday-u-")
      assert "evil-other-user" not in captured["manifest"]["spec"]["queue"]
  ```

  Use the real route / schema. If the Pydantic schema does not even allow a `queue` field, the API rejects unknown fields and this test is moot — confirm by inspecting `app/schemas/job.py::JobCreate`.

- [ ] **Step 3: If the schema accepts `queue`, harden.**

  Two possible fixes:
  - Remove `queue` from `JobCreate` schema (clean: rejects with 422 at validation).
  - Keep accepting it but ignore in `create_job` — server-side always overrides.

  Prefer **option A** (remove from schema). Update `app/schemas/job.py::JobCreate` to NOT have a `queue` field. With `extra="forbid"` (verify), clients sending it get a 422.

  Confirm there's no legitimate caller that supplies `queue` — grep frontend code:

  ```bash
  grep -rn '"queue"\|queue:' frontend/src/ | head
  ```

  If no frontend code references it and no test relies on it, dropping is safe.

- [ ] **Step 4: Confirm `build_volcano_job_manifest` derives queue from user.**

  Look for the manifest construction:

  ```bash
  grep -B2 -A15 'build_volcano_job_manifest\|"queue":' backend/app/services/job_spec.py
  ```

  Confirm `spec.queue` is set from `await ensure_user_queue(user.id)`. If it's read from a function argument that could be client-controlled, fix.

- [ ] **Step 5: Run, verify, commit.**

  ```bash
  cd backend && uv run pytest tests/test_jobs.py -v
  ```

  ```bash
  git add backend/app/schemas/job.py backend/app/services/job_spec.py backend/tests/test_jobs.py
  git commit -m "fix(backend): server-side Volcano queue enforcement [H-21]
  ```

JobCreate schema rejects client-supplied 'queue'; build_volcano_job_manifest
derives spec.queue exclusively from authenticated user via
ensure_user_queue(user.id). Regression test asserts attacker-supplied
queue names cannot leak into the vcjob manifest."

````

---

## Task 14: [M-internal-split] /internal sub-app on separate port

**Findings:** M-internal-split (MEDIUM).

**Files:**

- Create: `backend/app/internal_app.py`
- Modify: `backend/app/main.py` (drop the internal router mount from the main app)
- Modify: `backend/Dockerfile` (run two uvicorns)
- Create: `backend/entrypoint.sh`
- Modify: `charts/lolday/templates/backend.yaml` (expose port 8001, update Service, add NetworkPolicy allow rule)

**Rationale:** Today `/api/v1/internal/*` lives on the same listener as the public `/api/v1/*` API. If the Cloudflare tunnel ever maps `/` to backend (or if a NetworkPolicy misconfig exposes 8000), the internal endpoints become reachable from the public internet — defense rests entirely on `require_job_token`. Splitting onto port 8001 + a NetworkPolicy that allows only `lolday-jobs` provides defence in depth.

- [ ] **Step 1: Create the internal sub-app.**

Create `backend/app/internal_app.py`:

```python
"""A separate FastAPI app instance that hosts ONLY /api/v1/internal/*.

Bound to container port 8001 by the entrypoint. NetworkPolicy gates :8001
to lolday-jobs (callbacks) only; Cloudflared tunnel maps :8000 only.
"""

from fastapi import FastAPI

from app.routers import internal

internal_app = FastAPI(title="Lolday Internal", docs_url=None, redoc_url=None)
internal_app.include_router(
    internal.router,
    prefix="/api/v1/internal",
    tags=["internal"],
)


@internal_app.get("/livez", include_in_schema=False)
async def livez():
    return {"status": "ok"}
````

- [ ] **Step 2: Drop the internal router from the main app.**

  Modify `backend/app/main.py`. Find the block:

  ```python
  # Internal routes (build callbacks)
  app.include_router(
      internal.router,
      prefix="/api/v1/internal",
      tags=["internal"],
  )
  ```

  Delete it. Add at the same place:

  ```python
  # Internal routes have moved to internal_app (port 8001) — see app/internal_app.py.
  # /api/v1/internal/* is no longer served on the public port 8000.
  ```

- [ ] **Step 3: Add the entrypoint that runs both uvicorns.**

  Create `backend/entrypoint.sh`:

  ```sh
  #!/bin/sh
  set -e

  # Run public API on :8000 and internal API on :8001.
  # Each gets its own uvicorn process; SIGTERM is forwarded to both via trap.
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
  ```

  Make it executable: `chmod +x backend/entrypoint.sh`.

- [ ] **Step 4: Update the Dockerfile.**

  Replace the CMD line in `backend/Dockerfile` (currently:
  `CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`):

  ```dockerfile
  COPY --chown=lolday:lolday entrypoint.sh /app/entrypoint.sh
  RUN chmod +x /app/entrypoint.sh

  EXPOSE 8000 8001

  CMD ["/app/entrypoint.sh"]
  ```

  Also bump the `COPY app/` line to include the new `internal_app.py` automatically (it's already covered by `COPY app/ ./app/`).

- [ ] **Step 5: Update the Helm Deployment + Service.**

  In `charts/lolday/templates/backend.yaml`:
  - Container `ports` should list 8000 AND 8001.
  - Service should expose both ports with named entries:

  ```yaml
  ports:
    - containerPort: 8000
      name: api
    - containerPort: 8001
      name: internal
  ```

  ```yaml
  spec:
    type: ClusterIP
    ports:
      - name: api
        port: 8000
        targetPort: api
      - name: internal
        port: 8001
        targetPort: internal
    selector:
      app.kubernetes.io/component: backend
  ```

- [ ] **Step 6: Add NetworkPolicy for port 8001.**

  Add to `charts/lolday/templates/network-policy.yaml` after the existing `backend-metrics-from-monitoring-only` policy:

  ```yaml
  ---
  # M-internal-split: port 8001 is the /api/v1/internal sub-app, callable
  # only from job pods in lolday-jobs. Cloudflared tunnel maps port 8000 only.
  apiVersion: networking.k8s.io/v1
  kind: NetworkPolicy
  metadata:
    name: backend-internal-from-jobs-only
    namespace: { { .Values.global.namespace } }
    labels: { { - include "lolday.labels" . | nindent 4 } }
  spec:
    podSelector:
      matchLabels:
        app.kubernetes.io/component: backend
    policyTypes:
      - Ingress
    ingress:
      - from:
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name:
                  { { .Values.global.jobsNamespace } }
        ports:
          - port: 8001
            protocol: TCP
  ```

  And update the existing P1 `backend-metrics-from-monitoring-only` policy to remove the jobs-ns allow on port 8000 (jobs no longer hit 8000):

  Change the first rule from "Cloudflared + jobs" to "Cloudflared only":

  ```yaml
      - from:
          - namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: {{ .Values.global.namespace }}
          podSelector:
            matchLabels:
              app.kubernetes.io/component: cloudflared
        ports:
          - port: 8000
            protocol: TCP
  ```

  Remove the second from-block for `lolday-jobs` ns on port 8000.

- [ ] **Step 7: Update httpx callers inside job-helper.**

  Jobs reach the backend via `http://backend.lolday.svc:8000/api/v1/internal/...`. Update the URL to `http://backend.lolday.svc:8001/api/v1/internal/...`. Search:

  ```bash
  grep -rn "backend.lolday.svc:8000\|/api/v1/internal" charts/lolday/helpers/ backend/app/ 2>/dev/null
  ```

  Update every match to use port 8001. The most likely files are `charts/lolday/helpers/job-helper/job_helper/*.py` and possibly `services/job_spec.py` (init-container env vars).

- [ ] **Step 8: Run, lint, commit.**

  ```bash
  cd backend && uv run pytest tests/test_internal_events.py -v
  helm lint charts/lolday --set redis.auth.password=test-pwd
  ```

  ```bash
  git add backend/app/internal_app.py \
          backend/app/main.py \
          backend/Dockerfile \
          backend/entrypoint.sh \
          charts/lolday/templates/backend.yaml \
          charts/lolday/templates/network-policy.yaml \
          charts/lolday/helpers/job-helper/
  git commit -m "feat(backend,charts): split /internal to port 8001 [M-internal-split]
  ```

Internal callback API moves to a separate FastAPI instance on
container port 8001. NetworkPolicy gates 8001 to lolday-jobs only;
cloudflared tunnel maps 8000 only. job-helper updated to call 8001.

The require_job_token guard remains the auth boundary; this split
adds a network-layer gate in case the tunnel ever exposes more
than intended."

```

---

## Task 15: [H-15] MLflow Traefik ForwardAuth + backend authz endpoint

**Findings:** H-15 (HIGH). **This is the largest task in P2.**

**Files:**

- Create: `backend/app/routers/mlflow_authz.py`
- Modify: `backend/app/main.py` (register the new router)
- Create: `charts/lolday/templates/mlflow-forward-auth-middleware.yaml` (Traefik Middleware)
- Modify: `charts/lolday/templates/ingress.yaml` (chain the middleware onto the /mlflow/ route)
- Test: `backend/tests/test_mlflow_authz.py`

**Rationale:** MLflow's tracking server has no built-in authn. Today it relies on Cloudflare Access for external traffic (browser users), but cluster-internal traffic from job pods is implicitly trusted. A compromised detector can read/mutate/delete any run. Adding Traefik ForwardAuth in front of MLflow forces every request through a backend authz endpoint that verifies either a CF Access JWT (browser) or a job token (sidecar) before allowing the call.

### Architecture sketch

```

[browser] [job pod]
| |
| Cf-Access-Jwt-Assertion: ... | Authorization: Bearer <job-token>
| |
v v
[Traefik IngressRoute (host-based)] [Traefik in-cluster route or direct]
\ /
v v
[Traefik Middleware: ForwardAuth]
|
v
[POST /api/v1/mlflow-authz on backend:8000]
|
+----------+----------+
| |
200 403
| |
v v
[MLflow] [client gets 403]

````

The Traefik in-cluster route is achieved by also defining an IngressRoute that
matches a cluster-internal hostname (e.g. `mlflow-via-traefik`), forcing job
pods to use that hostname. The NetworkPolicy from T9 already restricts direct
mlflow Service access to backend + Traefik.

### Step 1: Author the backend `/api/v1/mlflow-authz` endpoint.

Create `backend/app/routers/mlflow_authz.py`:

```python
"""Traefik ForwardAuth target for MLflow access control.

Traefik calls ``POST /api/v1/mlflow-authz`` with the original request's
headers and the path/method in extra headers. We return 200 to allow,
403 to deny. The MLflow Service is locked down by NetworkPolicy so this
is the only path that can reach MLflow.
"""

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
from app.db import get_async_session
from app.deps import require_job_token
from app.models import Job, Role, User
from app.services.mlflow_client import MlflowClient, MlflowError
from app.services.job_tokens import verify_token
from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


# Match a few MLflow REST paths we care about.
RUN_PATH_RE = re.compile(r"/api/2\.0/mlflow/runs/(?P<run_id>[A-Za-z0-9_-]+)")
ARTIFACT_PATH_RE = re.compile(r"/api/2\.0/mlflow-artifacts/artifacts/[^/]+/(?P<run_id>[A-Za-z0-9_-]+)/")


def _mlflow():
    return MlflowClient(
        settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS
    )


async def _identify_via_cf(
    request: Request,
    session: AsyncSession,
) -> User | None:
    """Resolve a browser-driven request via CF Access JWT. Returns None on
    missing/invalid JWT; the caller maps that to 403."""
    token = request.headers.get("x-forwarded-cf-access-jwt-assertion")
    if not token:
        return None
    try:
        return await resolve_user_from_jwt(session, token, log_context="mlflow-authz")
    except CfAccessAuthError:
        return None


async def _identify_via_job_token(
    request: Request,
    session: AsyncSession,
) -> Job | None:
    """Resolve a job-pod-driven request via Authorization: Bearer <token>."""
    auth = request.headers.get("x-forwarded-authorization") or request.headers.get(
        "authorization"
    )
    if not auth or not auth.lower().startswith("bearer "):
        return None
    raw_token = auth[7:]
    # Find the Job by token hash via direct DB query (we don't have the job_id
    # in the path the way require_job_token's signature expects).
    from sqlalchemy import select
    from app.services.job_tokens import hash_token

    h = hash_token(raw_token)
    job = (
        await session.execute(select(Job).where(Job.token_hash == h))
    ).scalar_one_or_none()
    if job is None:
        return None
    # Defense-in-depth: reject terminal jobs.
    from app.models.job import NON_TERMINAL_STATUSES
    if job.status not in NON_TERMINAL_STATUSES:
        return None
    return job


def _extract_run_id_from_url(uri: str, query_string: str) -> str | None:
    """Find a run_id in either the URI path or the query string."""
    m = RUN_PATH_RE.search(uri) or ARTIFACT_PATH_RE.search(uri)
    if m:
        return m.group("run_id")
    # MLflow's update-run / log-metric / log-batch endpoints POST run_id in
    # the body, which Traefik does NOT forward to ForwardAuth. The middleware
    # has no body access. For now we deny if we can't extract run_id.
    return None


async def _run_owner_id(run_id: str) -> str | None:
    """Read the lolday.user_id tag from MLflow."""
    try:
        run = await _mlflow().get_run(run_id)
    except MlflowError:
        return None
    data = run.get("data") or {}
    tags_list = data.get("tags") or []
    tags = {t["key"]: t["value"] for t in tags_list if "key" in t}
    return tags.get("lolday.user_id")


@router.post("", include_in_schema=False)
async def mlflow_authz(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    x_forwarded_uri: Annotated[str, Header()] = "",
    x_forwarded_method: Annotated[str, Header()] = "GET",
) -> dict:
    """Decide whether the upstream MLflow request is allowed.

    Returns 200 with empty body for ALLOW, raises 403 for DENY.
    Traefik responds to the caller with the same status code.
    """
    user = await _identify_via_cf(request, session)
    if user is not None:
        if user.role == Role.ADMIN:
            return {"allow": True, "as": "admin"}
        run_id = _extract_run_id_from_url(x_forwarded_uri, request.url.query)
        if run_id is None:
            # Endpoints we can't resolve to a run_id are admin-only.
            raise HTTPException(status_code=403, detail="cannot resolve run for ACL check")
        owner = await _run_owner_id(run_id)
        if owner and owner == str(user.id):
            return {"allow": True, "as": "user", "run_id": run_id}
        raise HTTPException(status_code=403, detail="not run owner")

    job = await _identify_via_job_token(request, session)
    if job is not None:
        # Job pods can write to their own run only.
        run_id = _extract_run_id_from_url(x_forwarded_uri, request.url.query)
        if run_id is None or run_id != job.mlflow_run_id:
            raise HTTPException(status_code=403, detail="job-token scope mismatch")
        return {"allow": True, "as": "job", "run_id": run_id}

    raise HTTPException(status_code=403, detail="no recognized auth")
````

Register the router in `backend/app/main.py`, after the existing `experiments_proxy` mount (~line 264):

```python
from app.routers import mlflow_authz

app.include_router(
    mlflow_authz.router,
    prefix="/api/v1/mlflow-authz",
    tags=["mlflow-authz"],
)
```

### Step 2: Write the tests.

Create `backend/tests/test_mlflow_authz.py`:

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_mlflow_authz_denies_no_auth(user_client_no_auth: AsyncClient):
    """Without any auth header, 403."""
    r = await user_client_no_auth.post("/api/v1/mlflow-authz")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_mlflow_authz_allows_owner(user_client, mlflow_stub, monkeypatch):
    """A browser request from the run's owner gets 200."""
    user_id = user_client.user_id_str
    mlflow_stub.add_run(experiment_id="1", run_id="r-a", tags={"lolday.user_id": user_id})
    r = await user_client.post(
        "/api/v1/mlflow-authz",
        headers={
            "X-Forwarded-CF-Access-Jwt-Assertion": "...",  # adapt to test fixture
            "X-Forwarded-Uri": "/api/2.0/mlflow/runs/r-a",
            "X-Forwarded-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.json()["allow"] is True


@pytest.mark.asyncio
async def test_mlflow_authz_denies_non_owner(user_client, second_user_client, mlflow_stub):
    """A browser request from a non-owner gets 403."""
    mlflow_stub.add_run(experiment_id="1", run_id="r-a", tags={"lolday.user_id": str(user_client.user.id)})
    r = await second_user_client.post(
        "/api/v1/mlflow-authz",
        headers={
            "X-Forwarded-CF-Access-Jwt-Assertion": "...",
            "X-Forwarded-Uri": "/api/2.0/mlflow/runs/r-a",
            "X-Forwarded-Method": "GET",
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_mlflow_authz_admin_sees_all(admin_client, mlflow_stub):
    """Admin gets 200 regardless of run ownership."""
    mlflow_stub.add_run(experiment_id="1", run_id="r-a", tags={"lolday.user_id": "some-other-uuid"})
    r = await admin_client.post(
        "/api/v1/mlflow-authz",
        headers={
            "X-Forwarded-CF-Access-Jwt-Assertion": "...",
            "X-Forwarded-Uri": "/api/2.0/mlflow/runs/r-a",
        },
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_mlflow_authz_job_token_scoped(internal_client_factory):
    """A job token authenticates writes to the same run only."""
    client = await internal_client_factory()
    r_ok = await client.post(
        "/api/v1/mlflow-authz",
        headers={
            "Authorization": f"Bearer {client.token}",
            "X-Forwarded-Uri": f"/api/2.0/mlflow/runs/{client.mlflow_run_id}",
            "X-Forwarded-Method": "POST",
        },
    )
    assert r_ok.status_code == 200
    r_deny = await client.post(
        "/api/v1/mlflow-authz",
        headers={
            "Authorization": f"Bearer {client.token}",
            "X-Forwarded-Uri": "/api/2.0/mlflow/runs/some-other-run",
        },
    )
    assert r_deny.status_code == 403
```

Adapt fixture names to existing patterns in `conftest.py`.

### Step 3: Run the failing tests.

```bash
cd backend && uv run pytest tests/test_mlflow_authz.py -v
```

Expected: tests fail (endpoint not yet wired) — then pass after Step 1 lands.

### Step 4: Create the Traefik Middleware resource.

Create `charts/lolday/templates/mlflow-forward-auth-middleware.yaml`:

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: mlflow-forward-auth
  namespace: { { .Values.global.namespace } }
  labels: { { - include "lolday.labels" . | nindent 4 } }
spec:
  forwardAuth:
    address: http://backend.{{ .Values.global.namespace }}.svc.cluster.local:8000/api/v1/mlflow-authz
    authResponseHeaders:
      - X-Lolday-Authz-As
    authRequestHeaders:
      - Cf-Access-Jwt-Assertion
      - Authorization
```

### Step 5: Chain the middleware onto the /mlflow/ route.

Modify `charts/lolday/templates/ingress.yaml`. The existing `/mlflow` route currently has no middlewares. Add:

```yaml
- kind: Rule
  match: Host(`{{ .Values.frontend.host }}`) && PathPrefix(`/mlflow`)
  priority: 6
  middlewares:
    - name: mlflow-forward-auth
  services:
    - kind: Service
      name: mlflow
      port: { { .Values.mlflow.service.port } }
```

### Step 6: Lint, run, commit.

```bash
helm lint charts/lolday --set redis.auth.password=test-pwd
helm template charts/lolday --set redis.auth.password=test-pwd 2>/dev/null | grep -B2 -A10 "kind: Middleware"
cd backend && uv run pytest tests/test_mlflow_authz.py -v
cd backend && uv run pytest -q   # full suite
```

```bash
git add backend/app/routers/mlflow_authz.py \
        backend/app/main.py \
        backend/tests/test_mlflow_authz.py \
        charts/lolday/templates/mlflow-forward-auth-middleware.yaml \
        charts/lolday/templates/ingress.yaml
git commit -m "feat(backend,charts): MLflow Traefik ForwardAuth + per-user ACL [H-15]

New /api/v1/mlflow-authz endpoint that Traefik calls on every
/mlflow/* request. Resolves caller via either CF Access JWT
(browser) or Job bearer token (sidecar), reads the run's
lolday.user_id tag, denies on mismatch.

T9 (H-12) already restricts the MLflow Service to backend +
Traefik; job pods that previously connected to mlflow:5000
directly now go through Traefik and hit this auth gate."
```

---

## Task 16: [H-16] /mlflow/ method allowlist

**Findings:** H-16 (HIGH).

**Files:**

- Modify: `charts/lolday/templates/mlflow-forward-auth-middleware.yaml` (or new middleware)
- Modify: `charts/lolday/templates/ingress.yaml`

**Rationale:** With T15 in place, every /mlflow/ request hits the forward-auth gate. But the gate authorises owners + admins for ALL methods. Non-admins should not DELETE experiments or mutate the model registry through the UI. Add a second middleware that rejects POST/PATCH/DELETE for non-admin users.

- [ ] **Step 1: Decide where the gate lives.**

  Option A: extend `/api/v1/mlflow-authz` to also check the method header (`X-Forwarded-Method`) and 403 non-admins on mutating methods.
  Option B: add a separate Traefik `headers` Middleware that strips the method (no — middleware can't do that).
  Option C: a separate ForwardAuth chained second.

  Prefer **Option A** — keep all auth logic in one backend endpoint.

  Modify `backend/app/routers/mlflow_authz.py` `mlflow_authz` function. After resolving `user` (browser-driven path), add:

  ```python
      MUTATING_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
      method = (x_forwarded_method or "GET").upper()
      if user is not None and user.role != Role.ADMIN and method in MUTATING_METHODS:
          raise HTTPException(
              status_code=403,
              detail=f"non-admin users cannot {method} MLflow resources",
          )
  ```

  Place this check AFTER admin short-circuit and BEFORE the per-run ACL.

- [ ] **Step 2: Update tests.**

  Add to `backend/tests/test_mlflow_authz.py`:

  ```python
  @pytest.mark.asyncio
  async def test_mlflow_authz_non_admin_cannot_delete(user_client, mlflow_stub):
      mlflow_stub.add_run(
          experiment_id="1", run_id="r-a",
          tags={"lolday.user_id": user_client.user_id_str},
      )
      r = await user_client.post(
          "/api/v1/mlflow-authz",
          headers={
              "X-Forwarded-CF-Access-Jwt-Assertion": "...",
              "X-Forwarded-Uri": "/api/2.0/mlflow/runs/r-a",
              "X-Forwarded-Method": "DELETE",
          },
      )
      assert r.status_code == 403
      assert "cannot DELETE" in r.json()["detail"]
  ```

- [ ] **Step 3: Run, verify, commit.**

  ```bash
  cd backend && uv run pytest tests/test_mlflow_authz.py -v
  ```

  ```bash
  git add backend/app/routers/mlflow_authz.py backend/tests/test_mlflow_authz.py
  git commit -m "fix(backend): /mlflow/ method allowlist via mlflow_authz [H-16]
  ```

Non-admin users get 403 on POST/PATCH/PUT/DELETE through Traefik
ForwardAuth. Admins remain unrestricted. Job tokens (sidecar)
already scoped to their own run by H-15."

````

---

## P2 Done

After Task 16 lands, verify the whole phase end-to-end:

- [ ] **Step A: Full backend test suite.**

```bash
cd backend && uv run pytest -q
````

Expected: green.

- [ ] **Step B: helm lint.**

  ```bash
  helm lint charts/lolday --set redis.auth.password=test-pwd
  ```

  Expected: clean.

- [ ] **Step C: pre-commit on all files.**

  ```bash
  pre-commit run --all-files
  ```

  Expected: clean.

- [ ] **Step D: Cross-check finding IDs in commit history.**

  ```bash
  git log --oneline main..HEAD | grep -oE '\[[CHM][^]]+\]' | tr ',' '\n' | sort -u | tr -d '[]'
  ```

  Expected output (set):

  ```
  H-7  H-8  H-9  H-10  H-11  H-12  H-13  H-14  H-15  H-16  H-21
  M-alembic-hardening
  M-backend-np
  M-cloudflared-np
  M-internal-split
  M-minio-console
  M-mlflow-init-hardening
  ```

- [ ] **Step E: Open the PR.**

  Per the P1 pattern, push the branch + `gh pr create --base main`. PR body must call out:
  - The Redis password (`!`) breaking change — operator must `--set redis.auth.password=...`
  - The BuildKit seccomp DaemonSet — requires K3s nodes to have `/var/lib/kubelet/seccomp/` writable (verify with `ls -ld /var/lib/kubelet/seccomp/` on server30)
  - The PSS labels start at `enforce: baseline` and need manual promotion after 7-day audit
  - MLflow ForwardAuth installs a new admission-style gate; if backend is down, MLflow is unreachable

- [ ] **Step F: Post-deploy operator verification.**

  Document in the PR body and the deploy runbook:

  ```bash
  # PSS labels
  kubectl get ns lolday-jobs -o jsonpath='{.metadata.labels}' | jq

  # Default-deny lolday ns ingress
  kubectl run -n default --rm -i --restart=Never --image=busybox debug -- \
    sh -c 'nc -zv -w 3 postgresql.lolday.svc 5432 2>&1'
  # Expected: connection refused / timeout

  # MLflow ForwardAuth
  kubectl run -n lolday-jobs --rm -i --restart=Never --image=curlimages/curl debug -- \
    curl -sS --max-time 5 http://lolday-mlflow.lolday.svc:5000/api/2.0/mlflow/experiments/list
  # Expected: connection refused (NetworkPolicy)

  kubectl run -n lolday-jobs --rm -i --restart=Never --image=curlimages/curl debug -- \
    curl -sS --max-time 5 https://lolday.connlabai.com/mlflow/api/2.0/mlflow/experiments/list
  # Expected: 403 (ForwardAuth without CF JWT)

  # Backend port split
  kubectl exec -n lolday deploy/backend -- ss -tlnp 2>/dev/null
  # Expected: listening on both :8000 and :8001
  ```

---

## Notes for the implementer

- **PSS promotion is operator-driven, not chart-driven.** Don't try to flip `enforce: restricted` in the chart. It must wait for the 7-day audit window per `docs/runbooks/p2-pss-labels.md`.
- **Redis password is a breaking deploy.** Surface in PR body. Operator must rotate the secret on next `helm upgrade`.
- **MLflow ForwardAuth depends on the backend being healthy.** If backend is down, MLflow is unreachable. Document the trade-off in the PR body; this is the cost of ForwardAuth.
- **BuildKit seccomp DaemonSet uses hostPath at UID 0.** This will fail PSS `enforce: restricted` on the `lolday` ns. If T8's PSS label is promoted to enforce before this is migrated to a Job-on-master pattern, the DaemonSet pod will not start. Document the dependency.
- **Per-task TDD where applicable.** Chart-only tasks use `helm lint` + `helm template` + manual visual check; backend code tasks use `pytest`.

---

## Self-review (writing-plans skill)

**Spec coverage** — every P2 finding from spec §6.2 is covered:

| Finding                 | Task                                              |
| ----------------------- | ------------------------------------------------- |
| H-7                     | T4 (with scope adjustment — automount stays true) |
| H-8                     | T5                                                |
| H-9                     | T6                                                |
| H-10                    | T2                                                |
| H-11                    | T12                                               |
| H-12                    | T9                                                |
| H-13                    | T1                                                |
| H-14                    | T8                                                |
| H-15                    | T15                                               |
| H-16                    | T16                                               |
| H-21                    | T13                                               |
| M-backend-np            | T10                                               |
| M-cloudflared-np        | T11                                               |
| M-internal-split        | T14                                               |
| M-minio-console         | T3                                                |
| M-alembic-hardening     | T7                                                |
| M-mlflow-init-hardening | T7                                                |

**Placeholder scan:** every code step has the actual code; every shell step has the exact command. The `...` markers inside YAML re-renderings are followed by explicit preservation hints ("with existing rules / env unchanged") rather than implicit "fill in".

**Type consistency:** `EVENT_KIND` from P1 is not touched here; the new `RUN_PATH_RE` and `ARTIFACT_PATH_RE` are private to `mlflow_authz.py`; the helper `_extract_run_id_from_url` returns `str | None` consistently.

**Known fragilities:**

- T4 (H-7) defers `automountServiceAccountToken: false` because the backend talks to the K8s API directly. Tracked but not resolved here — a follow-up phase needs to migrate the K8s client to a narrower SA before the automount can be off.
- T8 (H-14) PSS labels start at `enforce: baseline` because BuildKit needs Unconfined seccomp until BuildKit moves to its own ns. The custom seccomp profile from T12 may unblock `enforce: restricted` for `lolday-jobs`; verify after T12 lands.
- T12 (H-11) BuildKit seccomp DaemonSet writes to a hostPath as UID 0. The compensating control is the narrow file scope. If your K3s setup needs `privileged: true` to do the hostPath write, escalate before implementing.
- T15 (H-15) Traefik ForwardAuth body inspection: MLflow's `update-run` / `log-metric` / `log-batch` endpoints POST `run_id` in the body. Traefik does NOT forward the body to ForwardAuth. The current regex extracts run_id from the URL only; mutating endpoints that don't include run_id in the URL will currently 403. If maldet uses those endpoints heavily (it should via the JSONL → /internal path, not directly to MLflow), this is moot. Verify empirically.
- T16 (H-16) the method allowlist applies only to CF-Access-driven (browser) requests. Job-token-driven requests are already scoped to their own run by T15, so DELETE on the same run is allowed for sidecars (intentional — a job can clean up after itself).

---
